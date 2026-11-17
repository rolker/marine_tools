r"""
CLI: convert recorded sidescan from a rosbag2 to an XTF file.

Reads ``marine_acoustic_msgs/RawSonarImage`` port and starboard channels
plus the TF tree, georeferences each ping via an ``earth`` (ECEF) frame
lookup of the ping's own ``header.frame_id``, pairs port with starboard,
and streams two-channel XTF ping packets to disk.

Usage::

    ros2 run bag_analysis bag_to_xtf \\
        --bag /path/to/bizzyboat_sonar/2026-06-15T15-03-45+00-00 \\
        --output ~/data/sidescan/2026-06-15.xtf

The bag must contain the TF chain from ``earth`` down to the sidescan
sensor frames (a self-contained ``bizzyboat_sonar`` bag does); a
sidescan-only ``*_sidescan_raw`` bag has no nav and cannot be
georeferenced.

Output is split into parts so no file gets too big for the receiving
tool: a new part starts when the next ping would push the file past
``--split-size`` (default 500 MB), when the part has run longer than
``--split-duration`` (default off), or when the sonar was silent for
more than ``--split-gap`` seconds (default 60). ``--no-split`` writes one
file. A run that ends with a single part keeps the plain ``--output``
name; with several parts every part is named after the UTC time of its
first ping (``survey_20260615T150345Z.xtf``).

Each part is streamed to a ``.partial`` file and renamed into place once
it is complete, so a crash never leaves a truncated-but-valid-looking
XTF behind. After a failure the finished parts are kept and listed, and
only the unfinished ``.partial`` is deleted. Files left by earlier runs
are never deleted; they are listed in a warning at the end. A run that
writes zero pings (wrong topics, no nav, all-malformed) exits non-zero.
"""

from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path
import re
import sys

import numpy as np
from rclpy.duration import Duration
import rclpy.time
from tf2_ros import Buffer, TransformException

from ..reader import iter_messages
from ..xtf.geo import ecef_pose_to_geo
from ..xtf.writer import (
    ChannelPing,
    LAYOUT_PINGMAPPER,
    LAYOUT_STANDARD,
    XtfWriter,
)

# marine_acoustic_msgs/SonarImageData.dtype -> numpy base type.
_DTYPE_MAP = {
    0: np.uint8, 1: np.int8, 2: np.uint16, 3: np.int16,
    4: np.uint32, 5: np.int32, 6: np.uint64, 7: np.int64,
    8: np.float32, 9: np.float64,
}

_DEFAULT_PORT = '/bizzy/sensors/sidescan/garmin_sidescan/sonar_image_port'
_DEFAULT_STBD = '/bizzy/sensors/sidescan/garmin_sidescan/sonar_image_starboard'
_DEFAULT_NADIR = '/bizzy/sensors/sidescan/garmin_sidescan/nadir_depth'
_DEFAULT_SOUND_SPEED = 1500.0
_NS_PER_S = 1_000_000_000
_DEFAULT_SPLIT_SIZE_MB = 500.0
_DEFAULT_SPLIT_GAP_S = 60.0
# File header (1024) + ping header (256) + two channel headers (2 x 64).
_MIN_PING_FILE_BYTES = 1024 + 256 + 128
_XTF_OFFSET_LIMIT = 2 ** 31


class _PendingPing:
    """A georeferenced single-channel ping awaiting its counterpart."""

    __slots__ = (
        't_ns', 'time', 'lat', 'lon', 'heading', 'pitch', 'roll',
        'ecef', 'samples', 'slant_range', 'frequency', 'time_delay',
        'time_duration', 'sound_velocity',
    )

    def __init__(self, **kw) -> None:
        """Populate slots from keyword arguments."""
        for k, v in kw.items():
            setattr(self, k, v)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='bag_to_xtf',
        description='Convert recorded sidescan from a rosbag2 to XTF.',
    )
    p.add_argument(
        '--bag', required=True, type=Path,
        help='Path to the rosbag2 directory (the one with metadata.yaml)',
    )
    p.add_argument(
        '--output', required=True, type=Path,
        help='Output XTF file path (e.g. .../survey.xtf)',
    )
    p.add_argument('--port-topic', default=_DEFAULT_PORT)
    p.add_argument('--starboard-topic', default=_DEFAULT_STBD)
    p.add_argument(
        '--nadir-topic', default=_DEFAULT_NADIR,
        help='sensor_msgs/Range topic used for altitude-above-bottom',
    )
    p.add_argument(
        '--earth-frame', default='earth',
        help='ECEF root frame for georeferencing (default: earth)',
    )
    p.add_argument(
        '--pair-tolerance', type=float, default=0.25,
        help='Max |dt| (s) on header stamps to pair port with starboard',
    )
    p.add_argument(
        '--max-tf-age', type=float, default=1.0,
        help=('Max age (s) of a fallback "latest" TF relative to the ping '
              'stamp before the ping is dropped as un-georeferenceable'),
    )
    p.add_argument(
        '--start-time', default=None,
        help=('Skip pings before this time. Accepts epoch seconds or an '
              'ISO-8601 UTC timestamp (e.g. 2026-06-15T16:26:49). Use to '
              'trim dock idle from the start of a survey.'),
    )
    p.add_argument(
        '--end-time', default=None,
        help='Skip pings after this time (same formats as --start-time).',
    )
    p.add_argument(
        '--tf-cache', type=float, default=600.0,
        help='TF buffer cache length in seconds',
    )
    p.add_argument(
        '--sonar-name', default='Garmin GCV sidescan',
        help='SonarName written to the XTF file header',
    )
    p.add_argument(
        '--max-pings', type=int, default=None,
        help='Stop after writing this many ping packets (for testing)',
    )
    p.add_argument(
        '--split-size', type=float, default=None, metavar='MB',
        help=('Start a new output file before it would exceed this many MB '
              '(1 MB = 10^6 bytes). Default 500; 0 turns the size limit off. '
              'Many XTF tools fail on files over 2 GB.'),
    )
    p.add_argument(
        '--split-duration', type=float, default=None, metavar='MINUTES',
        help=('Start a new output file when the current one spans this many '
              'minutes of pings. Default: off.'),
    )
    p.add_argument(
        '--split-gap', type=float, default=None, metavar='SECONDS',
        help=('Start a new output file when more than this many seconds pass '
              'between consecutive pings. Default 60; 0 turns it off.'),
    )
    p.add_argument(
        '--no-split', action='store_true',
        help=('Write a single output file, whatever its size. Cannot be '
              'combined with --split-size, --split-duration or --split-gap.'),
    )
    p.add_argument(
        '--layout', choices=(LAYOUT_STANDARD, LAYOUT_PINGMAPPER),
        default=LAYOUT_STANDARD,
        help=(
            'XTF channel layout. "standard" (default) is the spec-compliant '
            'interleaved layout read by pyxtf and standard XTF tools. '
            '"pingmapper" is a non-standard contiguous layout required by '
            'PINGVerter / PING-Mapper, which misread the standard layout; '
            'files written this way will mis-decode in standard XTF readers.'
        ),
    )
    return p


def _parse_args(argv):
    """
    Parse ``argv`` and resolve the split limits.

    Adds ``split_bytes``, ``split_duration_ns`` and ``split_gap_ns`` to the
    namespace (``None`` = that limit is off) and ``split_warnings`` (messages
    to print before the run starts).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    for name in ('split_size', 'split_duration', 'split_gap'):
        value = getattr(args, name)
        if value is not None and not value >= 0.0:
            parser.error(f'--{name.replace("_", "-")} must not be negative')
    if args.max_pings is not None and args.max_pings < 1:
        parser.error('--max-pings must be at least 1')
    explicit = [
        f'--{n.replace("_", "-")}'
        for n in ('split_size', 'split_duration', 'split_gap')
        if getattr(args, n) is not None]
    if args.no_split and explicit:
        parser.error(
            f'--no-split cannot be combined with {", ".join(explicit)}')

    size_mb = _DEFAULT_SPLIT_SIZE_MB if args.split_size is None \
        else args.split_size
    gap_s = _DEFAULT_SPLIT_GAP_S if args.split_gap is None \
        else args.split_gap
    duration_min = args.split_duration
    args.split_bytes = None
    args.split_duration_ns = None
    args.split_gap_ns = None
    args.split_warnings = []
    if args.no_split:
        return args
    if size_mb > 0:
        args.split_bytes = int(size_mb * 1_000_000)
        if args.split_bytes < _MIN_PING_FILE_BYTES:
            args.split_warnings.append(
                f'--split-size {size_mb} MB is smaller than a file header '
                'plus one ping; every ping will get its own part')
        if args.split_bytes > _XTF_OFFSET_LIMIT:
            args.split_warnings.append(
                f'--split-size {size_mb} MB is over 2 GB; many XTF tools '
                'use 32-bit file offsets and cannot read parts that large')
    if duration_min:
        args.split_duration_ns = int(duration_min * 60 * _NS_PER_S)
    if gap_s > 0:
        args.split_gap_ns = int(gap_s * _NS_PER_S)
    return args


def _decode_samples(msg) -> np.ndarray:
    """Decode a RawSonarImage image payload to a 1-D float array."""
    base = _DTYPE_MAP.get(msg.image.dtype, np.uint8)
    dtype = np.dtype(base).newbyteorder('>' if msg.image.is_bigendian else '<')
    arr = np.frombuffer(bytes(msg.image.data), dtype=dtype).astype(np.float64)
    beams = max(int(msg.image.beam_count), 1)
    if beams > 1 and arr.size % beams == 0:
        arr = arr.reshape(-1, beams).mean(axis=1)
    return arr


def _ping_geometry(msg, n_samples: int) -> tuple[float, float, float, float]:
    """Return (slant_range_m, time_delay_s, time_duration_s, sound_speed)."""
    sound_speed = msg.ping_info.sound_speed or _DEFAULT_SOUND_SPEED
    fs = float(msg.sample_rate)
    sample0 = int(msg.sample0)
    # Range to the last sample; the near-field gate (sample0) shifts the
    # window away from the transducer, so it must be included.
    slant_range = (sample0 + n_samples) * sound_speed / (2.0 * fs)
    time_delay = sample0 / fs            # round-trip time to first sample
    time_duration = n_samples / fs
    return slant_range, time_delay, time_duration, sound_speed


def _stamp_ns(stamp) -> int:
    """Nanoseconds since epoch from a builtin_interfaces/Time stamp."""
    return stamp.sec * _NS_PER_S + stamp.nanosec


def _parse_time(value):
    """
    Parse a CLI time to epoch nanoseconds, or None if not given.

    Accepts either epoch seconds (e.g. ``1750000000``) or an ISO-8601
    timestamp (e.g. ``2026-06-15T16:26:49``); a naive ISO time is read
    as UTC.
    """
    if value is None:
        return None
    try:
        return int(float(value) * 1e9)
    except ValueError:
        pass
    parsed = _dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return int(parsed.timestamp() * 1e9)


def _lookup_pose(buffer, frame, earth_frame, stamp, max_tf_age_ns):
    """
    Look up earth->frame at the ping stamp, with a bounded latest fallback.

    Returns ``(pose, status)`` where ``pose`` is
    ``((x, y, z), (qx, qy, qz, qw))`` or ``None``, and ``status`` is one
    of ``'exact'`` (stamped lookup), ``'approx'`` (fell back to the
    latest transform, within ``max_tf_age_ns``), ``'stale'`` (latest
    transform too old -> dropped), or ``'missing'`` (no transform at
    all). A bounded fallback prevents silently stamping a ping with a
    pose from an unrelated time when TF has a gap.
    """
    try:
        tf = buffer.lookup_transform(
            earth_frame, frame, rclpy.time.Time.from_msg(stamp))
        return _pose_from_tf(tf), 'exact'
    except TransformException:
        pass
    try:
        tf = buffer.lookup_transform(earth_frame, frame, rclpy.time.Time())
    except TransformException:
        return None, 'missing'
    ping_ns = _stamp_ns(stamp)
    age = abs(ping_ns - _stamp_ns(tf.header.stamp))
    if ping_ns > 0 and age > max_tf_age_ns:
        return None, 'stale'
    return _pose_from_tf(tf), 'approx'


def _pose_from_tf(tf):
    """Extract ((x, y, z), (qx, qy, qz, qw)) from a TransformStamped."""
    t = tf.transform.translation
    q = tf.transform.rotation
    return (t.x, t.y, t.z), (q.x, q.y, q.z, q.w)


def _make_pending(msg, bag_t_ns, pose) -> _PendingPing:
    """Build a georeferenced pending ping from a RawSonarImage + TF pose."""
    samples = _decode_samples(msg)
    slant_range, time_delay, time_duration, sound_speed = _ping_geometry(
        msg, samples.size)
    lat, lon, _alt, heading, pitch, roll = ecef_pose_to_geo(pose[0], pose[1])
    # Prefer the acquisition stamp; fall back to bag-receive time only if
    # the driver left the header unstamped.
    stamp_ns = _stamp_ns(msg.header.stamp) or bag_t_ns
    return _PendingPing(
        t_ns=stamp_ns,
        time=_dt.datetime.fromtimestamp(stamp_ns / 1e9, tz=_dt.timezone.utc),
        lat=lat, lon=lon, heading=heading, pitch=pitch, roll=roll,
        ecef=np.array(pose[0]), samples=samples, slant_range=slant_range,
        frequency=msg.ping_info.frequency, time_delay=time_delay,
        time_duration=time_duration, sound_velocity=sound_speed,
    )


def _speed_mps(a: _PendingPing, b: _PendingPing) -> float:
    """Ground speed between two pings from their ECEF displacement."""
    dt = abs(a.t_ns - b.t_ns) / 1e9
    if dt <= 0.0:
        return 0.0
    return float(np.linalg.norm(a.ecef - b.ecef) / dt)


# Nominal inter-ping interval (s) used for the first ping and across gaps,
# where the real spacing is unavailable or implausible. ~0.1 s is a
# representative sidescan ping period; any finite positive value works.
_DEFAULT_SECONDS_PER_PING = 0.1
# Gaps longer than this are treated as dropouts, not real ping intervals.
_MAX_SECONDS_PER_PING = 2.0


def _seconds_per_ping(prev: _PendingPing | None, cur: _PendingPing) -> float:
    """
    Inter-ping interval (s) for the XTF channel header.

    Derived from the time since the previously emitted ping; falls back to a
    nominal value for the first ping and across gaps so the field is always
    finite and positive. PINGVerter / PING-Mapper reject SecondsPerPing <= 0
    (they flag the whole ping as invalid geometry), so 0 must never be written.
    """
    if prev is None:
        return _DEFAULT_SECONDS_PER_PING
    dt = (cur.t_ns - prev.t_ns) / 1e9
    if not 0.0 < dt <= _MAX_SECONDS_PER_PING:
        return _DEFAULT_SECONDS_PER_PING
    return dt


def _iter_pings(args, counts):
    """
    Read the bag and yield ``(port, starboard, altitude)`` per paired ping.

    Owns everything up to the write: the TF buffer, nadir tracking, the
    ``--start-time``/``--end-time`` window, georeferencing and port/
    starboard pairing. ``altitude`` is the latest nadir range seen so far
    (``None`` before the first one). The pending pair is cleared before
    each ``yield`` so a consumer that stops early (``--max-pings``) does
    not see the just-yielded pair counted as ``unpaired``.
    """
    buffer = Buffer(cache_time=Duration(seconds=args.tf_cache))
    topics = [
        '/tf', '/tf_static', args.port_topic, args.starboard_topic,
        args.nadir_topic,
    ]
    max_tf_age_ns = int(args.max_tf_age * _NS_PER_S)
    pair_tol_ns = args.pair_tolerance * _NS_PER_S
    start_ns = _parse_time(args.start_time)
    end_ns = _parse_time(args.end_time)

    pending: dict = {'port': None, 'starboard': None}
    latest_altitude = None
    try:
        for topic, msg, t_ns in iter_messages(args.bag, topics=topics):
            if topic == '/tf':
                for tr in msg.transforms:
                    buffer.set_transform(tr, 'bag_to_xtf')
                continue
            if topic == '/tf_static':
                for tr in msg.transforms:
                    buffer.set_transform_static(tr, 'bag_to_xtf')
                continue
            if topic == args.nadir_topic:
                latest_altitude = float(msg.range)
                counts['nadir'] += 1
                continue

            if topic == args.port_topic:
                side = 'port'
            elif topic == args.starboard_topic:
                side = 'starboard'
            else:
                continue
            counts[side] += 1

            # Time-window trim (e.g. dock-out to dock-in). TF/nadir are fed
            # regardless above; only sidescan pings are gated. The bag is
            # time-ordered, so once past end_ns nothing more is needed.
            eff_ns = _stamp_ns(msg.header.stamp) or t_ns
            if start_ns is not None and eff_ns < start_ns:
                counts['out_of_window'] += 1
                continue
            if end_ns is not None and eff_ns > end_ns:
                break

            if float(msg.sample_rate) <= 0.0:
                counts['malformed'] += 1
                continue

            pose, status = _lookup_pose(
                buffer, msg.header.frame_id, args.earth_frame,
                msg.header.stamp, max_tf_age_ns)
            if pose is None:
                counts['no_tf' if status == 'missing' else 'stale_tf'] += 1
                continue
            if status == 'approx':
                counts['approx_tf'] += 1
            ping = _make_pending(msg, t_ns, pose)

            other = 'starboard' if side == 'port' else 'port'
            mate = pending[other]
            if mate is not None and abs(ping.t_ns - mate.t_ns) <= pair_tol_ns:
                port, stbd = (ping, mate) if side == 'port' else (mate, ping)
                pending['port'] = pending['starboard'] = None
                yield port, stbd, latest_altitude
            else:
                if pending[side] is not None:
                    counts['unpaired'] += 1  # discarded; mate never arrived
                pending[side] = ping
    finally:
        counts['unpaired'] += sum(1 for v in pending.values() if v is not None)


def _utc_label(when: _dt.datetime) -> str:
    """Return ``YYYYmmddTHHMMSSZ`` for a UTC datetime."""
    return when.strftime('%Y%m%dT%H%M%SZ')


def _utc_iso(when: _dt.datetime) -> str:
    """Return ``YYYY-mm-ddTHH:MM:SSZ`` for a UTC datetime."""
    return when.strftime('%Y-%m-%dT%H:%M:%SZ')


class _PartInfo:
    """A finished part: where it is and what it holds."""

    def __init__(self, path, pings, first, last, size, reason) -> None:
        """Record the part's path, ping count, time span, size and cause."""
        self.path = path
        self.pings = pings
        self.first = first
        self.last = last
        self.size = size
        self.reason = reason    # why this part started (None for part 1)


class _PartSink:
    """
    Write pings into a series of part files.

    A part is opened lazily, when its first ping is in hand, as a
    ``.partial`` named after that ping's UTC time. ``end_part`` closes it
    and renames it to its final time name. ``finish`` renames the only
    part of a one-part run to the plain output name. ``abort`` removes the
    unfinished ``.partial`` and leaves the finished parts alone.
    """

    def __init__(self, output: Path, sonar_name: str, layout: str) -> None:
        """Remember where to write; nothing is created yet."""
        self.output = output
        self._sonar_name = sonar_name
        self._layout = layout
        self.parts: list[_PartInfo] = []
        self._names: set[str] = set()
        self._stream = None
        self._partial: Path | None = None
        self._final: Path | None = None
        self._first = self._last = None
        self._first_t_ns = 0
        self._reason = None
        self.writer: XtfWriter | None = None

    @property
    def is_open(self) -> bool:
        """Return True while a part is being written."""
        return self.writer is not None

    @property
    def first_t_ns(self) -> int:
        """Return the stamp (ns) of the open part's first ping."""
        return self._first_t_ns

    def _final_path(self, when: _dt.datetime) -> Path:
        stem, suffix = self.output.stem, self.output.suffix
        base = f'{stem}_{_utc_label(when)}'
        name = f'{base}{suffix}'
        n = 2
        while name in self._names:
            name = f'{base}_{n}{suffix}'
            n += 1
        self._names.add(name)
        return self.output.with_name(name)

    def start_part(self, when, t_ns, reason) -> None:
        """Open the next part, named after its first ping at ``when``."""
        self._final = self._final_path(when)
        self._partial = self._final.with_name(self._final.name + '.partial')
        self._stream = open(self._partial, 'wb')
        self.writer = XtfWriter(
            self._stream, sonar_name=self._sonar_name, layout=self._layout)
        self._first = self._last = when
        self._first_t_ns = t_ns
        self._reason = reason

    def write_ping(self, **kwargs) -> None:
        """Write one ping into the open part."""
        self.writer.write_ping(**kwargs)
        self._last = kwargs['time']

    def end_part(self) -> None:
        """Close the open part and move it to its final time name."""
        writer, stream = self.writer, self._stream
        self.writer = self._stream = None
        stream.close()
        self._partial.replace(self._final)
        self.parts.append(_PartInfo(
            self._final, writer.ping_count, self._first, self._last,
            writer.bytes_written, self._reason))

    def finish(self) -> None:
        """End the open part; a one-part run takes the plain output name."""
        if self.is_open:
            self.end_part()
        if len(self.parts) == 1:
            only = self.parts[0]
            only.path.replace(self.output)
            only.path = self.output

    def abort(self) -> None:
        """Drop the unfinished part (if any); finished parts stay."""
        stream, self.writer, self._stream = self._stream, None, None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if self._partial is not None:
            self._partial.unlink(missing_ok=True)
            self._partial = None


def _rollover_reason(args, sink, prev_t_ns, t_ns, ping_bytes):
    """
    Return why the next ping must start a new part, or ``None``.

    Compared in integer nanoseconds against the previous *emitted* ping.
    The first limit reached wins: gap, then duration, then size.
    """
    if (args.split_gap_ns is not None and prev_t_ns is not None
            and t_ns - prev_t_ns > args.split_gap_ns):
        return 'gap'
    if (args.split_duration_ns is not None
            and t_ns - sink.first_t_ns >= args.split_duration_ns):
        return 'duration'
    writer = sink.writer
    if (args.split_bytes is not None and writer.ping_count > 0
            and writer.bytes_written + ping_bytes > args.split_bytes):
        return 'size'
    return None


def _convert(args, sink, counts) -> int:
    """Write every paired ping from the bag into parts; return the count."""
    total = 0
    prev_emitted = None

    pings = _iter_pings(args, counts)
    try:
        for port, stbd, altitude in pings:
            speed = _speed_mps(prev_emitted, port) if prev_emitted else 0.0
            seconds_per_ping = _seconds_per_ping(prev_emitted, port)
            if altitude is None:
                counts['no_altitude'] += 1
            port_chan = ChannelPing(
                samples=port.samples, slant_range_m=port.slant_range,
                frequency_hz=port.frequency, time_delay_s=port.time_delay,
                time_duration_s=port.time_duration,
                seconds_per_ping=seconds_per_ping)
            stbd_chan = ChannelPing(
                samples=stbd.samples, slant_range_m=stbd.slant_range,
                frequency_hz=stbd.frequency, time_delay_s=stbd.time_delay,
                time_duration_s=stbd.time_duration,
                seconds_per_ping=seconds_per_ping)

            reason = None
            if sink.is_open:
                reason = _rollover_reason(
                    args, sink, prev_emitted.t_ns if prev_emitted else None,
                    port.t_ns, XtfWriter.ping_size(port_chan, stbd_chan))
                if reason:
                    sink.end_part()
            if not sink.is_open:
                sink.start_part(port.time, port.t_ns, reason)

            sink.write_ping(
                time=port.time, ping_number=total,
                latitude_deg=port.lat, longitude_deg=port.lon,
                sensor_depth_m=0.0, altitude_m=altitude or 0.0,
                heading_deg=port.heading, pitch_deg=port.pitch,
                roll_deg=port.roll, speed_mps=speed,
                sound_velocity_mps=port.sound_velocity,
                port=port_chan, starboard=stbd_chan)
            total += 1
            counts['paired'] += 1
            prev_emitted = port
            if args.max_pings is not None and total >= args.max_pings:
                break
    finally:
        pings.close()
    return total


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``bag_to_xtf`` console script."""
    args = _parse_args(argv)
    if not args.bag.exists():
        print(f'error: bag not found: {args.bag}', file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for message in args.split_warnings:
        print(f'warning: {message}', file=sys.stderr)

    counts = {
        'port': 0, 'starboard': 0, 'paired': 0, 'no_tf': 0, 'stale_tf': 0,
        'approx_tf': 0, 'malformed': 0, 'unpaired': 0, 'nadir': 0,
        'no_altitude': 0, 'out_of_window': 0,
    }
    sink = _PartSink(args.output, args.sonar_name, args.layout)
    try:
        ping_count = _convert(args, sink, counts)
        sink.finish()
    except BaseException as exc:
        # Finished parts stay; only the unfinished .partial is removed.
        sink.abort()
        print(f'error: conversion failed: {exc!r}', file=sys.stderr)
        _report_parts(sink.parts, header='finished parts kept')
        _warn_old_files(args.output, sink.parts)
        if isinstance(exc, OSError):
            return 1
        raise

    if ping_count == 0:
        print('error: no pings written (check topics, nav/TF, sample_rate)',
              file=sys.stderr)
        _report(sink, ping_count, counts, args)
        return 1

    _report(sink, ping_count, counts, args)
    _warn_old_files(args.output, sink.parts)
    return 0


def _mb(size: int) -> str:
    return f'{size:,} bytes ({size / 1e6:.1f} MB)'


def _report_parts(parts, *, header=None, limit=None) -> None:
    """Print one line per part: file, pings, time span, size."""
    if header:
        print(f'{header}: {len(parts)}')
    for n, part in enumerate(parts, 1):
        note = ''
        if part.reason:
            note = f'  (new part: {part.reason})'
        if limit is not None and part.size > limit:
            note += '  (over the size limit: a single ping)'
        print(f'  part {n}: {part.path.name}  pings={part.pings}  '
              f'{_utc_iso(part.first)} to {_utc_iso(part.last)}  '
              f'{_mb(part.size)}{note}')


def _old_files(output: Path, written) -> list[Path]:
    """Return files from earlier runs that this run did not write."""
    stem, suffix = re.escape(output.stem), re.escape(output.suffix)
    part_re = re.compile(
        rf'{stem}_\d{{8}}T\d{{6}}Z(_\d+)?{suffix}(\.partial)?')
    keep = {p.name for p in written}
    found = []
    for path in sorted(output.parent.iterdir()):
        if path.name in keep:
            continue
        if (part_re.fullmatch(path.name)
                or path.name in (output.name, output.name + '.partial')):
            found.append(path)
    return found


def _warn_old_files(output: Path, parts) -> None:
    """Warn about earlier runs' files next to the output; delete nothing."""
    old = _old_files(output, [p.path for p in parts])
    if not old:
        return
    print('WARNING: files from an earlier run are still in '
          f'{output.parent}; nothing was deleted:', file=sys.stderr)
    for path in old:
        print(f'  {path.name}', file=sys.stderr)
    print('  Remove them yourself if they are out of date, so they are not '
          'mixed up with the parts just written.', file=sys.stderr)


def _report(sink, ping_count: int, counts: dict, args) -> None:
    """Print a conversion summary, including any data loss."""
    parts = sink.parts
    if len(parts) == 1:
        print(f'wrote {ping_count} XTF pings -> {parts[0].path}')
    elif parts:
        print(f'wrote {ping_count} XTF pings in {len(parts)} parts '
              f'-> {sink.output.parent}')
    else:
        print(f'wrote {ping_count} XTF pings -> {sink.output}')
    _report_parts(parts, limit=args.split_bytes)
    print(f'  port msgs={counts["port"]} starboard msgs={counts["starboard"]}'
          f' paired={counts["paired"]}')
    print(f'  dropped: no_tf={counts["no_tf"]} stale_tf={counts["stale_tf"]} '
          f'malformed={counts["malformed"]} unpaired={counts["unpaired"]}')
    if counts['out_of_window']:
        print(f'  trimmed: {counts["out_of_window"]} pings outside '
              '--start-time/--end-time window')
    if counts['approx_tf']:
        print(f'  note: {counts["approx_tf"]} pings used a latest-TF fallback '
              '(within --max-tf-age)')
    if counts['nadir'] == 0:
        print('  warning: no nadir_depth messages; altitude written as 0 for '
              'all pings')
    elif counts['no_altitude']:
        print(f'  warning: {counts["no_altitude"]} pings written before the '
              'first nadir_depth (altitude 0)')


if __name__ == '__main__':
    sys.exit(main())
