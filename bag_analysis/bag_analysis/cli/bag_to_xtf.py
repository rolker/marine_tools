r"""
CLI: convert recorded sidescan from a rosbag2 to an XTF file.

Reads ``marine_acoustic_msgs/RawSonarImage`` port and starboard channels
plus the TF tree, georeferences each ping via an ``earth`` (ECEF) frame
lookup of the ping's own ``header.frame_id``, pairs port with starboard,
and streams two-channel XTF ping packets to disk.

The pose is the one at the ping's time. Transforms past that time are later
in the bag, so each ping is held until transforms on both sides of its time
have been read (at most ``--tf-wait`` seconds of bag time), then looked up
with tf2 at that time. There is no fallback to the latest transform: a ping
whose transforms are further apart than ``--max-tf-gap`` around its time,
or missing, is dropped and counted by reason. ``--stamp-to-transmit-offset``
moves the lookup (and the ping time written) to ``stamp - offset`` for a
sonar whose stamp is the time the host received the ping, not the time it
was sent; the stamp as recorded is kept in the ping header's ComputerClock
fields. Window, pairing: raw stamp. Written time, part names, split gap and
duration, speed: stamp - offset.

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

Each part is streamed to a ``.partial`` file, synced to disk and renamed
into place once it is complete, so a crash never leaves a
truncated-but-valid-looking XTF behind. After a failure the finished
parts are kept and listed, and only the unfinished ``.partial`` is
deleted.

The run refuses to start (exit 2, nothing written) if the output folder
already holds the plain ``--output`` file or any file of this output's
part-name pattern (parts and ``.partial`` leftovers), unless
``--overwrite`` is given. With ``--overwrite`` the run replaces the files
whose names it produces and lists them (an old ``.partial`` is unlinked,
never written through); files of the pattern that it did not regenerate
are never deleted, only warned about. Without ``--overwrite`` a file that
appears mid-run is not replaced: parts move into place by hard link, else
by an atomic no-replace rename (Linux), and only where neither exists by a
check followed by a rename. A finished part that cannot take its name is
kept under its ``.partial`` name and reported.
A run that writes zero pings (wrong topics, no nav, all-malformed) exits
non-zero.
"""

from __future__ import annotations

import argparse
from collections import deque
import ctypes
import ctypes.util
import datetime as _dt
import errno
import math
import os
from pathlib import Path
import re
import sys
import tempfile

import numpy as np

from ..reader import iter_messages
from ..tf_lookup import (
    DEFINITE, MAX_CACHE_S, NO_TF, READY, stamp_ns as _stamp_ns, TF_EVICTED,
    TF_GAP, TF_LATE, TfChain, TfChainError, TfMismatch, WAITS)
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
# Largest accepted split limits; anything bigger is a typo, and 0 /
# --no-split already mean "no limit".
_MAX_SPLIT_SIZE_MB = 1e7
_MAX_SPLIT_DURATION_MIN = 1e7
_MAX_SPLIT_GAP_S = 1e9
# How many file names an error message lists before saying "and N more".
_MAX_LISTED = 10
# Largest stamp-to-transmit offset (s) accepted; anything bigger is a typo.
_MAX_OFFSET_S = 3600.0


class _PendingPing:
    """A georeferenced single-channel ping awaiting its counterpart."""

    __slots__ = (
        't_ns', 'time', 'raw_ns', 'computer_time', 'lat', 'lon', 'heading', 'pitch', 'roll',
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
        allow_abbrev=False,
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
        '--max-tf-gap', type=float, default=1.0, metavar='SECONDS',
        help=('Largest allowed interval (s) between the two transform '
              'samples that bracket the ping time, on every dynamic link of '
              'the chain; a ping in a longer gap is dropped (counted '
              'tf_gap). Default 1, about ten periods of a 10 Hz chain.'),
    )
    p.add_argument(
        '--max-tf-age', default=None, help=argparse.SUPPRESS)
    p.add_argument(
        '--tf-wait', type=float, default=5.0, metavar='SECONDS',
        help=('How long (s of bag time) a ping waits for transforms past its '
              'time before it is dropped (counted tf_late). Must be larger '
              'than --max-tf-gap. With a negative '
              '--stamp-to-transmit-offset the pose is looked up that much '
              'after the stamp, and the wait is extended by the same amount. '
              'Default 5.'),
    )
    p.add_argument(
        '--stamp-to-transmit-offset', type=float, default=0.0,
        metavar='SECONDS',
        help=('Seconds the ping stamp lags the moment the ping was '
              'transmitted (the Garmin stamp is a host receive time). The '
              'pose is looked up at stamp - offset and that is also the '
              'ping time written; the stamp itself goes to the ComputerClock '
              'fields. Negative values are allowed. Default 0.'),
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
        '--overwrite', action='store_true',
        help=('Allow replacing files in the output folder. Without it the '
              'run refuses to start if the plain --output file or any file '
              "of this output's part-name pattern (parts, .partial "
              'leftovers) already exists. With it, the files this run '
              'produces replace same-named ones and are listed; other old '
              'files of the pattern are only warned about, never deleted.'),
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
    caps = {
        'split_size': _MAX_SPLIT_SIZE_MB,
        'split_duration': _MAX_SPLIT_DURATION_MIN,
        'split_gap': _MAX_SPLIT_GAP_S,
    }
    for name, cap in caps.items():
        value = getattr(args, name)
        flag = f'--{name.replace("_", "-")}'
        if value is None:
            continue
        if not math.isfinite(value):
            parser.error(f'{flag} must be a finite number')
        if value < 0.0:
            parser.error(f'{flag} must not be negative')
        if value > cap:
            parser.error(
                f'{flag} {value:g} is absurdly large (maximum {cap:g}); use '
                '0 or --no-split to remove a limit')
    if args.max_pings is not None and args.max_pings < 1:
        parser.error('--max-pings must be at least 1')
    if args.max_tf_age is not None:
        parser.error(
            '--max-tf-age is gone: it bounded the age of a fallback to the '
            'latest transform, which no longer exists. Use --max-tf-gap '
            '(the largest allowed interval between the two transform '
            'samples around the ping time) instead; its meaning is '
            'different, so do not carry the old value over')
    for flag, value in (
            ('--max-tf-gap', args.max_tf_gap), ('--tf-wait', args.tf_wait),
            ('--tf-cache', args.tf_cache),
            ('--stamp-to-transmit-offset', args.stamp_to_transmit_offset)):
        if not math.isfinite(value):
            parser.error(f'{flag} must be a finite number')
    if args.max_tf_gap <= 0.0:
        parser.error('--max-tf-gap must be greater than 0')
    if round(args.max_tf_gap * _NS_PER_S) < 1:
        parser.error(
            f'--max-tf-gap {args.max_tf_gap:g} rounds to 0 ns; it must be at '
            'least 1 ns')
    if args.tf_cache > MAX_CACHE_S:
        parser.error(
            f'--tf-cache {args.tf_cache:g} is absurdly large (maximum '
            f'{MAX_CACHE_S:g} s)')
    for flag, value in (('--start-time', args.start_time),
                        ('--end-time', args.end_time)):
        try:
            _parse_time(value)
        except (ValueError, OverflowError):
            parser.error(
                f'{flag} {value!r} is not epoch seconds or an ISO-8601 '
                'timestamp')
    if abs(args.stamp_to_transmit_offset) > _MAX_OFFSET_S:
        parser.error(
            f'--stamp-to-transmit-offset {args.stamp_to_transmit_offset:g} '
            f'is absurdly large (limit {_MAX_OFFSET_S:g} s either way)')
    if args.tf_wait <= args.max_tf_gap:
        parser.error(
            f'--tf-wait ({args.tf_wait:g} s) must be larger than '
            f'--max-tf-gap ({args.max_tf_gap:g} s): a ping has to be able '
            'to wait for the next sample of a chain that is allowed that '
            'far apart')
    if args.tf_cache <= (args.tf_wait + args.max_tf_gap
                         + abs(args.stamp_to_transmit_offset)
                         + args.pair_tolerance):
        parser.error(
            f'--tf-cache ({args.tf_cache:g} s) must be larger than '
            '--tf-wait + --max-tf-gap + |--stamp-to-transmit-offset| + '
            '--pair-tolerance, or a waiting ping could outlive the '
            'transforms it needs')
    args.offset_ns = round(args.stamp_to_transmit_offset * _NS_PER_S)
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
        args.split_duration_ns = round(duration_min * 60 * _NS_PER_S)
        if args.split_duration_ns < 1:
            parser.error(
                f'--split-duration {duration_min:g} rounds to 0 ns; every '
                'ping would start a new part')
    if gap_s > 0:
        args.split_gap_ns = round(gap_s * _NS_PER_S)
        if args.split_gap_ns < 1:
            parser.error(
                f'--split-gap {gap_s:g} rounds to 0 ns; every ping would '
                'start a new part')
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


_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


def _utc_from_ns(ns: int) -> _dt.datetime:
    """
    Return the UTC time of ``ns`` since the epoch, cut to microseconds.

    Integer arithmetic only: a float of seconds carries about 240 ns of
    error at today's epoch and rounds to the microsecond, which moved an
    instant just under a hundredth of a second, or a second, over the line.
    """
    return _EPOCH + _dt.timedelta(microseconds=ns // 1000)


def _make_pending(msg, raw_ns, offset_ns, pose) -> _PendingPing:
    """
    Build a georeferenced pending ping from a RawSonarImage + TF pose.

    ``raw_ns`` is the stamp as recorded; the ping time written is
    ``raw_ns - offset_ns``, the instant the pose was looked up at.
    """
    samples = _decode_samples(msg)
    slant_range, time_delay, time_duration, sound_speed = _ping_geometry(
        msg, samples.size)
    lat, lon, _alt, heading, pitch, roll = ecef_pose_to_geo(pose[0], pose[1])
    ping_ns = raw_ns - offset_ns
    return _PendingPing(
        t_ns=ping_ns, raw_ns=raw_ns,
        time=_utc_from_ns(ping_ns), computer_time=_utc_from_ns(raw_ns),
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


class _Held:
    """One sonar message, read from the bag and not yet paired or looked up."""

    __slots__ = ('side', 'msg', 'raw_ns', 'record_ns', 'lookup_ns',
                 'altitude')

    def __init__(self, side, msg, raw_ns, record_ns, offset_ns, altitude):
        """Remember the message, its stamps and the altitude seen with it."""
        self.side = side
        self.msg = msg
        self.raw_ns = raw_ns
        self.record_ns = record_ns
        self.lookup_ns = raw_ns - offset_ns
        self.altitude = altitude


class _HeldPing:
    """
    A port and a starboard message paired on their stamps, awaiting a pose.

    Which two messages form a ping is decided on the stamps as recorded and
    nothing else, so a missing transform can never change it. The pair is
    georeferenced, or dropped, as a whole. ``record_ns`` (bag time) is that
    of the message that completed the pair, and ``altitude`` the nadir range
    seen with it.
    """

    __slots__ = ('port', 'stbd', 'record_ns', 'altitude')

    def __init__(self, port, stbd, completing):
        """Pair ``port`` with ``stbd``; ``completing`` was read last."""
        self.port = port
        self.stbd = stbd
        self.record_ns = completing.record_ns
        self.altitude = completing.altitude


def _ping_verdict(item, chain, final):
    """
    Return the verdict for a held ping, from the verdicts of both halves.

    A definite refusal of either half (port first) is the ping's. Otherwise
    a half that still waits makes the ping wait, and ``final`` turns that
    wait into its answer. Only when both halves are ready is it ``ready``.
    """
    verdicts = [chain.check(h.msg.header.frame_id, h.lookup_ns)
                for h in (item.port, item.stbd)]
    for verdict in verdicts:
        if verdict in DEFINITE:
            return verdict
    for verdict in verdicts:
        if verdict in WAITS:
            return chain.settle(verdict) if final else verdict
    return READY


def _resolve(held, chain, counts, now_ns, wait_ns, final):
    """
    Release pings from the front of ``held`` whose answer is known.

    Yields ``(ping, port_pose, stbd_pose)`` for each one that can be
    georeferenced and counts the others, once per ping, by reason. Strictly
    first in, first out: a ping that still waits blocks those behind it, so
    order is as read. A wait ends when ``now_ns`` (bag record time) is more
    than ``wait_ns`` past the ping's own record time, or when ``final`` says
    no more transforms will come; the wait is then settled as ``no_tf`` or
    ``tf_late``.
    """
    while held:
        head = held[0]
        verdict = _ping_verdict(head, chain, final=False)
        if verdict in WAITS:
            if not final and now_ns - head.record_ns <= wait_ns:
                return
            verdict = _ping_verdict(head, chain, final=True)
        held.popleft()
        if verdict == READY:
            try:
                poses = [chain.lookup(h.msg.header.frame_id, h.lookup_ns)
                         for h in (head.port, head.stbd)]
            except TfMismatch:
                counts['tf_mismatch'] += 1
                continue
            yield head, poses[0], poses[1]
        else:
            counts[verdict] += 1


def _pair_by_stamp(pending, half, args, counts):
    """
    Pair a message with the waiting one of the other channel, by stamp.

    Returns the :class:`_HeldPing` when it completes a pair, else ``None``
    after storing the message; a message of the same channel it replaces was
    never mated and is counted ``unpaired`` (per message). Pairing compares
    the stamps as recorded and never looks at transforms.
    """
    side = half.side
    mate = pending['starboard' if side == 'port' else 'port']
    pair_tol_ns = args.pair_tolerance * _NS_PER_S
    if mate is not None and abs(half.raw_ns - mate.raw_ns) <= pair_tol_ns:
        pending['port'] = pending['starboard'] = None
        return _HeldPing(*((half, mate) if side == 'port' else (mate, half)),
                         completing=half)
    if pending[side] is not None:
        counts['unpaired'] += 1  # discarded; mate never arrived
    pending[side] = half
    return None


def _georeference(item, port_pose, stbd_pose, args):
    """Return the ``(port, starboard)`` pending pings of a resolved pair."""
    return (_make_pending(item.port.msg, item.port.raw_ns, args.offset_ns,
                          port_pose),
            _make_pending(item.stbd.msg, item.stbd.raw_ns, args.offset_ns,
                          stbd_pose))


def _iter_pings(args, counts):
    """
    Read the bag and yield ``(port, starboard, altitude)`` per paired ping.

    Owns everything up to the write: the TF chain, nadir tracking, the
    ``--start-time``/``--end-time`` window, port/starboard pairing and
    georeferencing. Pairing comes first and uses the stamps alone; the pair
    then waits in a queue until the transforms past its lookup time
    (stamp - offset) have been read, and gets a stamped tf2 lookup for each
    half; see ``_resolve``. A pair whose pose cannot be found is dropped as
    a whole and counted once under its reason. ``altitude`` is the latest
    nadir range seen when the completing message was read (``None`` before
    the first one). The pending pair is cleared before each ``yield`` so a
    consumer that stops early (``--max-pings``) does not see the
    just-yielded pair counted as ``unpaired``; pings still waiting are
    discarded without being counted.
    """
    chain = TfChain(args.earth_frame, args.tf_cache, args.max_tf_gap)
    topics = [
        '/tf', '/tf_static', args.port_topic, args.starboard_topic,
        args.nadir_topic,
    ]
    # A negative offset looks the pose up after the stamp, so the wait is
    # counted from the lookup time, not the record time.
    wait_ns = round(args.tf_wait * _NS_PER_S) + max(0, -args.offset_ns)
    start_ns = _parse_time(args.start_time)
    end_ns = _parse_time(args.end_time)

    pending: dict = {'port': None, 'starboard': None}
    held: deque = deque()
    latest_altitude = None
    admitting = True
    stopped_early = False
    try:
        for topic, msg, t_ns in iter_messages(args.bag, topics=topics):
            if topic == '/tf':
                chain.feed(msg.transforms, static=False,
                           authority='bag_to_xtf')
            elif topic == '/tf_static':
                chain.feed(msg.transforms, static=True,
                           authority='bag_to_xtf')
            elif not admitting:
                pass    # past --end-time: nothing new, but time still runs
            elif topic == args.nadir_topic:
                latest_altitude = float(msg.range)
                counts['nadir'] += 1
            elif topic in (args.port_topic, args.starboard_topic):
                side = 'port' if topic == args.port_topic else 'starboard'
                counts[side] += 1
                # Time-window trim (e.g. dock-out to dock-in), on the stamp
                # as recorded. TF/nadir are fed regardless above; only
                # sidescan pings are gated.
                raw_ns = _stamp_ns(msg.header.stamp) or t_ns
                if start_ns is not None and raw_ns < start_ns:
                    counts['out_of_window'] += 1
                elif end_ns is not None and raw_ns > end_ns:
                    admitting = False
                elif float(msg.sample_rate) <= 0.0:
                    counts['malformed'] += 1
                else:
                    pair = _pair_by_stamp(
                        pending,
                        _Held(side, msg, raw_ns, t_ns, args.offset_ns,
                              latest_altitude),
                        args, counts)
                    if pair is not None:
                        held.append(pair)
            else:
                continue

            for item, port_pose, stbd_pose in _resolve(
                    held, chain, counts, t_ns, wait_ns, final=False):
                port, stbd = _georeference(item, port_pose, stbd_pose, args)
                yield port, stbd, item.altitude
            if not admitting and not held:
                break

        for item, port_pose, stbd_pose in _resolve(
                held, chain, counts, 0, wait_ns, final=True):
            port, stbd = _georeference(item, port_pose, stbd_pose, args)
            yield port, stbd, item.altitude
    except GeneratorExit:
        stopped_early = True    # the consumer stopped (--max-pings)
        raise
    finally:
        # Halves read ahead while the last pair waited for its transforms
        # are not counted when the consumer stops the run: they are waiting
        # items, like the held pings.
        if not stopped_early:
            counts['unpaired'] += sum(
                1 for v in pending.values() if v is not None)
        counts['tf_rejected'] = dict(chain.rejected)


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


def _fsync_dir(path: Path) -> None:
    """Flush a directory entry to disk, best effort (not all systems can)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _settled(src: Path, dst: Path) -> bool:
    """
    Return True if moving ``src`` to ``dst`` has in fact completed.

    Used after an interrupted move to find out what the disk says. A
    hard-link move interrupted between the link and the unlink leaves
    both names on one file; the leftover ``src`` is removed here.
    """
    try:
        if not os.path.lexists(src):
            return os.path.lexists(dst)
        if os.path.lexists(dst) and os.path.samefile(src, dst):
            src.unlink()
            return True
    except OSError:
        pass
    return False


# fsync results that mean "this filesystem cannot do it" (some FUSE and
# SMB mounts), as opposed to EIO and friends, which mean data may be lost.
_FSYNC_UNSUPPORTED = frozenset(
    {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP})
# Errors of a no-replace rename that mean "not available here": try the
# next way. Anything else (EEXIST aside) is a real failure.
_NOREPLACE_UNSUPPORTED = frozenset(
    {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP,
     errno.EPERM, errno.EXDEV, errno.ENOTTY})
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1


def _fsync_file(stream) -> None:
    """Sync a file; tolerate filesystems that cannot, keep EIO fatal."""
    try:
        os.fsync(stream.fileno())
    except OSError as exc:
        if exc.errno not in _FSYNC_UNSUPPORTED:
            raise


def _get_renameat2():
    """Return libc ``renameat2`` (Linux), or None if it is not there."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c') or None,
                           use_errno=True)
        func = libc.renameat2
    except (OSError, AttributeError):
        return None
    func.argtypes = [ctypes.c_int, ctypes.c_char_p,
                     ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    func.restype = ctypes.c_int
    return func


def _rename_noreplace(src: Path, dst: Path) -> bool:
    """
    Rename ``src`` to ``dst`` atomically, failing if ``dst`` exists.

    Returns True when done. Raises ``FileExistsError`` if ``dst`` exists.
    Returns False if the kernel, libc or filesystem cannot do an atomic
    no-replace rename, so the caller must try something else.
    """
    func = _get_renameat2()
    if func is None:
        return False
    result = func(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst),
                  _RENAME_NOREPLACE)
    if result == 0:
        return True
    err = ctypes.get_errno()
    if err == errno.EEXIST:
        raise FileExistsError(errno.EEXIST, 'File exists', str(dst))
    if err in _NOREPLACE_UNSUPPORTED:
        return False
    raise OSError(err, os.strerror(err), str(dst))


class _PartSink:
    """
    Write pings into a series of part files.

    A part is opened lazily, when its first ping is in hand, as a
    ``.partial`` named after that ping's UTC time. ``end_part`` syncs and
    closes it and moves it to its final time name. ``finish`` moves the
    only part of a one-part run to the plain output name. ``abort``
    removes the unfinished ``.partial`` and leaves the finished parts
    alone.

    Without ``overwrite`` ``.partial`` files are opened exclusively and a
    part is moved into place without replacing: by hard link (atomic),
    else by an atomic no-replace rename (Linux ``renameat2``), else, only
    if neither exists on this filesystem, by a check followed by a
    rename, which leaves a window of microseconds. With ``overwrite`` an
    existing ``.partial`` is unlinked (never opened, so a symlink or hard
    link is never written through) and recorded in ``replaced``; a final
    move replaces by rename, which swaps the name and never writes
    through it.

    A finished part whose final name was taken meanwhile stays under its
    ``.partial`` name and is listed in ``kept``.
    """

    def __init__(
            self, output: Path, sonar_name: str, layout: str,
            overwrite: bool = False) -> None:
        """Remember where to write; nothing is created yet."""
        self.output = output
        self._sonar_name = sonar_name
        self._layout = layout
        self._overwrite = overwrite
        self.parts: list[_PartInfo] = []
        self.replaced: list[Path] = []
        self.kept: list[tuple[Path, _PartInfo]] = []
        self.discarded: tuple[Path, int] | None = None
        self.cleanup_errors: list[str] = []
        self._names: set[str] = set()
        self._stream = None
        self._partial: Path | None = None
        self._final: Path | None = None
        self._pings = 0
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
        final = self._final_path(when)
        partial = final.with_name(final.name + '.partial')
        if self._overwrite and os.path.lexists(partial):
            # Never open an existing name for writing: that would follow
            # a symlink or truncate a hard-linked inode. Remove the name
            # (the link, not its target), then create it afresh.
            partial.unlink()
            self.replaced.append(partial)
        stream = open(partial, 'xb')        # never truncate a foreign file
        # Only now is the .partial ours; abort() must not touch it before.
        self._final, self._partial, self._stream = final, partial, stream
        self._pings = 0
        self.writer = XtfWriter(
            stream, sonar_name=self._sonar_name, layout=self._layout)
        self._first = self._last = when
        self._first_t_ns = t_ns
        self._reason = reason

    def write_ping(self, **kwargs) -> None:
        """Write one ping into the open part."""
        self.writer.write_ping(**kwargs)
        self._pings += 1
        self._last = kwargs['time']

    def _place(self, src: Path, dst: Path) -> None:
        """Move ``src`` to ``dst``; replace an existing ``dst`` only if allowed."""
        if self._overwrite:
            existed = os.path.lexists(dst)
            os.replace(src, dst)    # swaps the name; never writes through it
            if existed:
                self.replaced.append(dst)
            return
        try:
            os.link(src, dst)
        except FileExistsError:
            raise
        except OSError:
            # No hard links here. Next best: an atomic no-replace rename.
            if _rename_noreplace(src, dst):
                return
            # Last resort: check, then rename. A file created in between
            # is replaced (a window of microseconds).
            if os.path.lexists(dst):
                raise FileExistsError(
                    errno.EEXIST, 'File exists', str(dst)) from None
            os.replace(src, dst)
            return
        os.unlink(src)

    def end_part(self) -> None:
        """Sync and close the open part and move it to its final time name."""
        writer, stream = self.writer, self._stream
        self.writer = self._stream = None
        try:
            stream.flush()
            _fsync_file(stream)
        finally:
            stream.close()
        partial, final = self._partial, self._final
        info = _PartInfo(
            final, writer.ping_count, self._first, self._last,
            writer.bytes_written, self._reason)
        try:
            self.parts.append(info)
            self._place(partial, final)
        except BaseException as exc:
            # An interrupt can land on either side of the move: let the
            # disk decide whether this part is finished.
            done = _settled(partial, final)
            if done and info not in self.parts:
                self.parts.append(info)
            elif not done and info in self.parts:
                self.parts.remove(info)
            if done:
                self._partial = None
            elif isinstance(exc, FileExistsError):
                # The part is complete and synced; only its name was
                # taken. Keep the data under its .partial name.
                self.kept.append((partial, info))
                self._partial = None
            raise
        self._partial = None
        _fsync_dir(final.parent)

    def finish(self) -> None:
        """End the open part; a one-part run takes the plain output name."""
        if self.is_open:
            self.end_part()
        if len(self.parts) == 1:
            only = self.parts[0]
            src = only.path
            try:
                self._place(src, self.output)
            except BaseException:
                if _settled(src, self.output):
                    only.path = self.output
                raise
            only.path = self.output
            _fsync_dir(self.output.parent)

    def abort(self) -> None:
        """
        Drop the unfinished part (if any); finished parts stay.

        Never raises for a file-system problem: trouble is recorded in
        ``cleanup_errors`` so it cannot hide the error that got us here.
        """
        stream, self.writer, self._stream = self._stream, None, None
        if stream is not None:
            try:
                stream.close()
            except OSError as exc:
                self.cleanup_errors.append(f'could not close part: {exc}')
        if self._partial is not None:
            self.discarded = (self._partial, self._pings)
            try:
                self._partial.unlink(missing_ok=True)
            except OSError as exc:
                self.cleanup_errors.append(
                    f'could not remove {self._partial}: {exc}')
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
                port=port_chan, starboard=stbd_chan,
                computer_time=port.computer_time)
            total += 1
            counts['paired'] += 1
            prev_emitted = port
            if args.max_pings is not None and total >= args.max_pings:
                break
    finally:
        pings.close()
    return total


def _list_names(paths, indent='  ') -> str:
    """Return the names, one per line; a long list is cut with a count."""
    names = [p.name for p in paths]
    lines = [f'{indent}{n}' for n in names[:_MAX_LISTED]]
    if len(names) > _MAX_LISTED:
        lines.append(f'{indent}... and {len(names) - _MAX_LISTED} more '
                     f'({len(names)} files in all)')
    return '\n'.join(lines)


def _preflight(args) -> int | None:
    """
    Check the output location before the bag is read.

    Returns an exit code (2) if the run must not start, else ``None``.
    Nothing is written, apart from the output folder being created and a
    temporary probe file that is removed again.
    """
    output = args.output
    if output.is_dir():
        print(f'error: --output is a directory: {output}', file=sys.stderr)
        return 2
    folder = output.parent
    if folder.is_dir():
        try:
            existing = _old_files(output, [])
        except OSError as exc:
            print(f'error: cannot read output folder {folder}: {exc}',
                  file=sys.stderr)
            return 2
        if existing and not args.overwrite:
            print(
                f'error: {len(existing)} file(s) for this output already '
                f'exist in {folder}:\n{_list_names(existing)}\n'
                'Nothing was written. Move them away, choose another '
                '--output, or pass --overwrite to let this run replace the '
                'files it produces.', file=sys.stderr)
            return 2
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError as exc:
        print(f'error: output folder {folder} is not writable: {exc}',
              file=sys.stderr)
        return 2
    return None


def _best_effort(func, *a, **kw) -> None:
    """Run a reporting step; a failure in it is noted, never raised."""
    try:
        func(*a, **kw)
    except Exception as exc:    # noqa: BLE001 - must not mask the real error
        print(f'warning: {func.__name__} failed: {exc!r}', file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``bag_to_xtf`` console script."""
    args = _parse_args(argv)
    if not args.bag.exists():
        print(f'error: bag not found: {args.bag}', file=sys.stderr)
        return 2
    refused = _preflight(args)
    if refused is not None:
        return refused
    for message in args.split_warnings:
        print(f'warning: {message}', file=sys.stderr)

    counts = {
        'port': 0, 'starboard': 0, 'paired': 0, NO_TF: 0, TF_GAP: 0,
        TF_LATE: 0, TF_EVICTED: 0, 'tf_mismatch': 0, 'malformed': 0, 'unpaired': 0,
        'nadir': 0, 'no_altitude': 0, 'out_of_window': 0,
    }
    sink = _PartSink(args.output, args.sonar_name, args.layout, args.overwrite)
    try:
        ping_count = _convert(args, sink, counts)
        sink.finish()
    except BaseException as exc:
        # Say what went wrong first; nothing below may hide it.
        if isinstance(exc, TfChainError):
            print(f'error: unsupported transform tree: {exc}',
                  file=sys.stderr)
        else:
            print(f'error: conversion failed: {exc!r}', file=sys.stderr)
        if isinstance(exc, FileExistsError):
            print('error: a file appeared in the output folder during the '
                  'run and was NOT replaced (pass --overwrite to allow '
                  'that)', file=sys.stderr)
        # Finished parts stay; only the unfinished .partial is removed.
        sink.abort()
        if sink.discarded is not None:
            path, pings = sink.discarded
            print(f'discarded unfinished part {path.name}: {pings} pings '
                  'were in it and are not written anywhere')
        for note in sink.cleanup_errors:
            print(f'warning: {note}', file=sys.stderr)
        _best_effort(_report_parts, sink.parts, header='finished parts kept')
        _best_effort(_report_kept, sink)
        _best_effort(_report_replaced, sink)
        _check_old_files(args.output, sink)
        if isinstance(exc, (OSError, TfChainError)):
            return 1
        raise

    if ping_count == 0:
        print('error: no pings written (check topics, nav/TF, sample_rate)',
              file=sys.stderr)
        _summarize(sink, ping_count, counts, args)
        _check_old_files(args.output, sink)
        return 1

    _summarize(sink, ping_count, counts, args)
    _check_old_files(args.output, sink)
    return 0


def _summarize(sink, ping_count, counts, args) -> None:
    """Print the summary; if that fails, still say the parts are written."""
    try:
        _report(sink, ping_count, counts, args)
    except Exception as exc:    # noqa: BLE001 - the data is already safe
        print(f'warning: the summary could not be printed ({exc!r}); the '
              f'{len(sink.parts)} part(s) were written:', file=sys.stderr)
        _best_effort(_report_parts, sink.parts)


def _check_old_files(output: Path, sink) -> None:
    """Warn about old files; a listing error is reported, never raised."""
    try:
        _warn_old_files(output, sink.parts, [p for p, _ in sink.kept])
    except Exception as exc:    # noqa: BLE001 - must not undo a finished run
        print(f'warning: the check for files of earlier runs could not be '
              f'done ({exc!r}); the output folder may hold old files that '
              'mix with the parts just written', file=sys.stderr)


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


def _warn_old_files(output: Path, parts, kept=()) -> None:
    """Warn about earlier runs' files next to the output; delete nothing."""
    old = _old_files(output, [p.path for p in parts] + list(kept))
    if not old:
        return
    print(f'WARNING: {output.parent} now mixes files from two runs. These '
          "files match this output's name pattern but were not written by "
          f'this run ({len(old)} in all; not regenerated, nothing was '
          'deleted):', file=sys.stderr)
    print(_list_names(old), file=sys.stderr)
    print('  Remove them yourself if they are out of date, so they are not '
          'mixed up with the parts just written.', file=sys.stderr)


def _report_kept(sink) -> None:
    """List complete parts left under their .partial name."""
    if not sink.kept:
        return
    print(f'{len(sink.kept)} finished part(s) could not take their final '
          'name (another file appeared there during the run, and was not '
          'replaced). They are complete data and were left under their '
          '.partial name; rename them yourself:')
    for path, info in sink.kept:
        print(f'  {path.name}  pings={info.pings}  '
              f'{_utc_iso(info.first)} to {_utc_iso(info.last)}  '
              f'{_mb(info.size)}')


def _report_replaced(sink) -> None:
    """List the files of an earlier run that this run replaced."""
    if sink.replaced:
        print(f'replaced {len(sink.replaced)} file(s) of an earlier run '
              '(--overwrite):')
        print(_list_names(sink.replaced))


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
    _report_replaced(sink)
    print(f'  port msgs={counts["port"]} starboard msgs={counts["starboard"]}'
          f' paired={counts["paired"]} (pings)')
    print('  dropped pings (a port and a starboard message paired on their '
          f'stamps, counted once): no_tf={counts[NO_TF]} '
          f'tf_gap={counts[TF_GAP]} tf_late={counts[TF_LATE]} '
          f'tf_evicted={counts[TF_EVICTED]} '
          f'tf_mismatch={counts["tf_mismatch"]}')
    print('  dropped messages (no ping formed): '
          f'malformed={counts["malformed"]} unpaired={counts["unpaired"]}')
    print(f'  stamp-to-transmit offset: {args.stamp_to_transmit_offset:g} s '
          '(pose looked up at stamp - offset; that is the ping time '
          'written, the stamp is in the ComputerClock fields)')
    if counts['out_of_window']:
        print(f'  trimmed: {counts["out_of_window"]} pings outside '
              '--start-time/--end-time window')
    if counts.get('tf_rejected'):
        reasons = ', '.join(
            f'{k}={v}' for k, v in sorted(counts['tf_rejected'].items()))
        print(f'  note: transforms rejected or re-fed: {reasons}')
    if counts['nadir'] == 0:
        print('  warning: no nadir_depth messages; altitude written as 0 for '
              'all pings')
    elif counts['no_altitude']:
        print(f'  warning: {counts["no_altitude"]} pings written before the '
              'first nadir_depth (altitude 0)')


if __name__ == '__main__':
    sys.exit(main())
