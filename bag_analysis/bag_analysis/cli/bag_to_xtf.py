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

The output is written to a ``.partial`` sibling and atomically renamed
into place only after a clean run, so a crash mid-stream never leaves a
truncated-but-valid-looking XTF behind. A run that writes zero pings
(wrong topics, no nav, all-malformed) exits non-zero.
"""

from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path
import sys

import numpy as np
from rclpy.duration import Duration
import rclpy.time
from tf2_ros import Buffer, TransformException

from ..reader import iter_messages
from ..xtf.geo import ecef_pose_to_geo
from ..xtf.writer import ChannelPing, XtfWriter

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
    return p


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


def _convert(args, stream, counts) -> int:
    """Run the read/georeference/pair/write loop; return pings written."""
    buffer = Buffer(cache_time=Duration(seconds=args.tf_cache))
    topics = [
        '/tf', '/tf_static', args.port_topic, args.starboard_topic,
        args.nadir_topic,
    ]
    max_tf_age_ns = int(args.max_tf_age * _NS_PER_S)
    pair_tol_ns = args.pair_tolerance * _NS_PER_S
    start_ns = _parse_time(args.start_time)
    end_ns = _parse_time(args.end_time)

    writer = XtfWriter(stream, sonar_name=args.sonar_name)
    pending: dict = {'port': None, 'starboard': None}
    latest_altitude = None
    prev_emitted = None

    def emit(port: _PendingPing, stbd: _PendingPing) -> None:
        nonlocal prev_emitted
        speed = _speed_mps(prev_emitted, port) if prev_emitted else 0.0
        if latest_altitude is None:
            counts['no_altitude'] += 1
        writer.write_ping(
            time=port.time, ping_number=writer.ping_count,
            latitude_deg=port.lat, longitude_deg=port.lon,
            sensor_depth_m=0.0, altitude_m=latest_altitude or 0.0,
            heading_deg=port.heading, pitch_deg=port.pitch,
            roll_deg=port.roll, speed_mps=speed,
            sound_velocity_mps=port.sound_velocity,
            port=ChannelPing(
                samples=port.samples, slant_range_m=port.slant_range,
                frequency_hz=port.frequency, time_delay_s=port.time_delay,
                time_duration_s=port.time_duration),
            starboard=ChannelPing(
                samples=stbd.samples, slant_range_m=stbd.slant_range,
                frequency_hz=stbd.frequency, time_delay_s=stbd.time_delay,
                time_duration_s=stbd.time_duration),
        )
        counts['paired'] += 1
        prev_emitted = port

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
            emit(port, stbd)
            pending['port'] = pending['starboard'] = None
        else:
            if pending[side] is not None:
                counts['unpaired'] += 1  # discarded; mate never arrived
            pending[side] = ping

        if args.max_pings is not None and writer.ping_count >= args.max_pings:
            break

    counts['unpaired'] += sum(1 for v in pending.values() if v is not None)
    return writer.ping_count


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``bag_to_xtf`` console script."""
    args = _build_parser().parse_args(argv)
    if not args.bag.exists():
        print(f'error: bag not found: {args.bag}', file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)

    counts = {
        'port': 0, 'starboard': 0, 'paired': 0, 'no_tf': 0, 'stale_tf': 0,
        'approx_tf': 0, 'malformed': 0, 'unpaired': 0, 'nadir': 0,
        'no_altitude': 0, 'out_of_window': 0,
    }
    # Write to a sibling .partial and rename on success, so a crash never
    # leaves a truncated file at the destination path.
    partial = args.output.with_name(args.output.name + '.partial')
    try:
        with open(partial, 'wb') as stream:
            ping_count = _convert(args, stream, counts)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    if ping_count == 0:
        partial.unlink(missing_ok=True)
        print('error: no pings written (check topics, nav/TF, sample_rate)',
              file=sys.stderr)
        _report(args.output, ping_count, counts)
        return 1

    partial.replace(args.output)
    _report(args.output, ping_count, counts)
    return 0


def _report(output: Path, ping_count: int, counts: dict) -> None:
    """Print a conversion summary, including any data loss."""
    print(f'wrote {ping_count} XTF pings -> {output}')
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
