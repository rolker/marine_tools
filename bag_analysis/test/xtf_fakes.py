"""
Synthetic bag streams for the bag_to_xtf tests.

``fake_messages`` builds the ``(topic, msg, t_ns)`` tuples that
``bag_analysis.reader.iter_messages`` would yield for a short sidescan
run: one ``earth -> sonar`` transform per ping, a port and a starboard
``RawSonarImage``-shaped message, and a nadir range. The sensor moves
along the ground at ``speed_mps``, so speed and seconds-per-ping are
non-trivial and a split that resets the carry-over changes the bytes.
"""

import math
import struct
from types import SimpleNamespace

from bag_analysis.xtf.geo import (
    ecef_pose_to_geo, ecef_to_enu_rotation, geodetic_to_ecef)
from builtin_interfaces.msg import Time as TimeMsg
from geometry_msgs.msg import TransformStamped
import numpy as np

PORT_TOPIC = '/p'
STBD_TOPIC = '/s'
NADIR_TOPIC = '/n'
FRAME = 'sonar'
T0_NS = 1_750_000_000 * 1_000_000_000

_ORIGIN = geodetic_to_ecef(43.0, -71.0, 0.0)


def _stamp(t_ns):
    return TimeMsg(sec=t_ns // 1_000_000_000, nanosec=t_ns % 1_000_000_000)


def _tf_msg(t_ns, east_m):
    tf = TransformStamped()
    tf.header.stamp = _stamp(t_ns)
    tf.header.frame_id = 'earth'
    tf.child_frame_id = FRAME
    tf.transform.translation.x = _ORIGIN[0]
    tf.transform.translation.y = _ORIGIN[1] + east_m
    tf.transform.translation.z = _ORIGIN[2]
    tf.transform.rotation.w = 1.0
    return SimpleNamespace(transforms=[tf])


def _sonar_msg(t_ns, index, n_samples):
    values = (np.arange(n_samples) * 7 + index * 13) % 4000
    return SimpleNamespace(
        header=SimpleNamespace(stamp=_stamp(t_ns), frame_id=FRAME),
        sample_rate=20000.0, sample0=0,
        ping_info=SimpleNamespace(sound_speed=1500.0, frequency=455000.0),
        image=SimpleNamespace(
            dtype=2, is_bigendian=False, beam_count=1,
            data=values.astype('<u2').tobytes()))


def fake_messages(
        n_pings, *, dt_s=0.2, speed_mps=1.5, start_ns=T0_NS,
        extra_gaps=None, drop_starboard=(), samples=lambda i: 40 + i % 9):
    """
    Return a list of ``(topic, msg, t_ns)`` for ``n_pings`` ping pairs.

    ``extra_gaps`` maps a ping index to extra seconds of silence before
    that ping. ``drop_starboard`` lists ping indices with no starboard
    message. ``samples(i)`` gives the sample count of ping ``i``.
    """
    out = []
    t_ns = start_ns
    east_m = 0.0
    gaps = extra_gaps or {}
    for i in range(n_pings):
        step_ns = int(dt_s * 1e9) + int(gaps.get(i, 0) * 1e9)
        t_ns += step_ns
        east_m += speed_mps * step_ns / 1e9
        out.append(('/tf', _tf_msg(t_ns, east_m), t_ns))
        out.append((NADIR_TOPIC, SimpleNamespace(range=3.0 + i * 0.01), t_ns))
        out.append((PORT_TOPIC, _sonar_msg(t_ns, i, samples(i)), t_ns))
        if i not in drop_starboard:
            out.append((STBD_TOPIC, _sonar_msg(t_ns, i + 500, samples(i)), t_ns))
    return out


def base_args(bag, output, *extra):
    """Return the CLI argument list for a synthetic run."""
    return [
        '--bag', str(bag), '--output', str(output),
        '--port-topic', PORT_TOPIC, '--starboard-topic', STBD_TOPIC,
        '--nadir-topic', NADIR_TOPIC, *extra,
    ]


# ---------------------------------------------------------------------------
# A moving, turning track sampled at 10 Hz, for the pose-at-ping-time tests.
# ---------------------------------------------------------------------------

_LAT0, _LON0 = 43.0, -71.0
_R_ECEF_TO_ENU = ecef_to_enu_rotation(_LAT0, _LON0)
_NS = 1_000_000_000


def _quat_from_matrix(m):
    """Return (x, y, z, w) for a rotation matrix (Shepperd's method)."""
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0.0:
        s = 2.0 * math.sqrt(trace + 1.0)
        return np.array([(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s,
                         (m[1, 0] - m[0, 1]) / s, 0.25 * s])
    i = int(np.argmax([m[0, 0], m[1, 1], m[2, 2]]))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = 2.0 * math.sqrt(1.0 + m[i, i] - m[j, j] - m[k, k])
    q = np.zeros(4)
    q[i] = 0.25 * s
    q[j] = (m[j, i] + m[i, j]) / s
    q[k] = (m[k, i] + m[i, k]) / s
    q[3] = (m[k, j] - m[j, k]) / s
    return q


def slerp(q0, q1, f):
    """Spherical interpolation along the short arc (own code, not tf2)."""
    q0, q1 = np.asarray(q0, float), np.asarray(q1, float)
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1, dot = -q1, -dot
    if dot > 0.9995:
        q = q0 + f * (q1 - q0)
        return q / np.linalg.norm(q)
    theta = math.acos(dot)
    return (math.sin((1 - f) * theta) * q0 + math.sin(f * theta) * q1) \
        / math.sin(theta)


class Track:
    """
    A boat track: poses of ``FRAME`` in ``earth`` every ``dt_s`` seconds.

    The boat turns at ``turn_deg_s`` and moves at ``speed_mps``, so one
    sample interval of staleness is a visible heading and position error.
    Samples listed in ``flip`` carry the negated quaternion (the same
    rotation), which a naive component-wise interpolation gets wrong.
    """

    def __init__(self, n_samples, *, dt_s=0.1, speed_mps=4.0,
                 turn_deg_s=15.0, flip=(), start_ns=T0_NS):
        self.dt_ns = int(round(dt_s * _NS))
        self.stamps = [start_ns + k * self.dt_ns for k in range(n_samples)]
        self.positions, self.quats = [], []
        east = north = 0.0
        for k in range(n_samples):
            t = k * dt_s
            heading = math.radians(30.0 + turn_deg_s * t)
            if k:
                east += speed_mps * dt_s * math.sin(heading)
                north += speed_mps * dt_s * math.cos(heading)
            self.positions.append(
                np.array(_ORIGIN) + _R_ECEF_TO_ENU.T @ [east, north, 0.0])
            rz = np.array([[math.cos(heading), -math.sin(heading), 0],
                           [math.sin(heading), math.cos(heading), 0],
                           [0, 0, 1.0]])
            enu_from_ned = np.array([[0, 1.0, 0], [1.0, 0, 0], [0, 0, -1.0]])
            q = _quat_from_matrix(_R_ECEF_TO_ENU.T @ enu_from_ned @ rz)
            self.quats.append(-q if k in flip else q)

    def pose_at(self, stamp_ns, skip=()):
        """
        Return (position, quaternion) interpolated between samples.

        Samples whose index is in ``skip`` are treated as absent, so the
        bracket is the nearest samples that remain.
        """
        k = int((stamp_ns - self.stamps[0]) // self.dt_ns)
        if self.stamps[k] == stamp_ns and k not in skip:
            return self.positions[k], self.quats[k]
        lo, hi = k, k + 1
        while lo in skip:
            lo -= 1
        while hi in skip:
            hi += 1
        f = (stamp_ns - self.stamps[lo]) / (self.stamps[hi] - self.stamps[lo])
        pos = (1 - f) * self.positions[lo] + f * self.positions[hi]
        return pos, slerp(self.quats[lo], self.quats[hi], f)

    def geo_at(self, stamp_ns, skip=()):
        """Return (lat, lon, alt, heading, pitch, roll) at ``stamp_ns``."""
        pos, quat = self.pose_at(stamp_ns, skip)
        return ecef_pose_to_geo(tuple(pos), tuple(quat))

    def tf_msg(self, k, child=FRAME, parent='earth'):
        """Return the /tf message for sample ``k``."""
        tf = TransformStamped()
        tf.header.stamp = _stamp(self.stamps[k])
        tf.header.frame_id = parent
        tf.child_frame_id = child
        (tf.transform.translation.x, tf.transform.translation.y,
         tf.transform.translation.z) = (float(v) for v in self.positions[k])
        (tf.transform.rotation.x, tf.transform.rotation.y,
         tf.transform.rotation.z, tf.transform.rotation.w) = (
            float(v) for v in self.quats[k])
        return SimpleNamespace(transforms=[tf])


def track_messages(
        track, n_pings, *, ping_dt_s=0.1, fractions=(0.3, 0.7),
        tf_delay_s=0.25, ping_lag_s=0.01, late_samples=(), first_ping=1,
        child=FRAME):
    """
    Return ``(messages, ping_stamps)`` for a track as the real bag records it.

    Each /tf sample is recorded ``tf_delay_s`` after its stamp,
    each ping ``ping_lag_s`` after its stamp, so pings come in the bag
    before the /tf past them and several pings are waiting at once.

    Ping ``i`` is stamped between samples ``i`` and ``i + 1`` at the
    ``fractions[i % len]`` position, so no ping sits on a sample.
    ``late_samples`` lists sample indices recorded after the sample that
    follows them (out-of-order /tf stamps).
    """
    entries = []
    for k in range(len(track.stamps)):
        rec = track.stamps[k] + int(tf_delay_s * _NS)
        entries.append((rec, 0, k, '/tf', track.tf_msg(k, child=child)))
    for k in late_samples:
        rec_next = track.stamps[k + 1] + int(tf_delay_s * _NS)
        entries[k] = (rec_next + 1, 0, k, '/tf', entries[k][4])
    ping_stamps = []
    for n in range(n_pings):
        i = first_ping + n
        stamp = track.stamps[0] + i * track.dt_ns + int(
            round(fractions[n % len(fractions)] * track.dt_ns))
        ping_stamps.append(stamp)
        rec = stamp + int(ping_lag_s * _NS)
        entries.append((rec, 1, n, NADIR_TOPIC,
                        SimpleNamespace(range=3.0 + n * 0.01)))
        entries.append((rec, 2, n, PORT_TOPIC, _sonar_msg(stamp, n, 40)))
        entries.append((rec, 3, n, STBD_TOPIC, _sonar_msg(stamp, n + 500, 40)))
    entries.sort(key=lambda e: e[:3])
    return [(e[3], e[4], e[0]) for e in entries], ping_stamps


_PING_HEADER = 256


def read_ping_headers(path):
    """Parse every ping header of an XTF file into a dict of the fields."""
    data = open(path, 'rb').read()
    offset, out = 1024, []
    while offset < len(data):
        h = data[offset:offset + _PING_HEADER]
        year, = struct.unpack_from('<H', h, 14)
        month, day, hour, minute, second, hsec = struct.unpack_from(
            '<6B', h, 16)
        out.append({
            'time': (year, month, day, hour, minute, second, hsec),
            'ping_number': struct.unpack_from('<I', h, 28)[0],
            'altitude': struct.unpack_from('<f', h, 196)[0],
            'heading': struct.unpack_from('<f', h, 124)[0],
            'lat': struct.unpack_from('<d', h, 128)[0],
            'lon': struct.unpack_from('<d', h, 136)[0],
            'pitch': struct.unpack_from('<f', h, 204)[0],
            'roll': struct.unpack_from('<f', h, 208)[0],
            'clock': struct.unpack_from('<4B', h, 236),
        })
        offset += struct.unpack_from('<I', h, 10)[0]
    return out


def two_frame_messages(track, pings, *, drop_port_samples=(),
                       drop_stbd_samples=(), tf_delay_s=0.25,
                       ping_lag_s=0.01):
    """
    Return /tf and ping messages for a port and a starboard sensor frame.

    The sensors sit in frames ``sonar_port`` and ``sonar_stbd``, both with
    the track's pose, so a transform sample can be missing for one channel
    only. ``pings`` lists ``(port_stamp_ns, stbd_stamp_ns)`` per ping;
    ``drop_port_samples`` and ``drop_stbd_samples`` are track sample
    indices left out of that frame's /tf.
    """
    entries = []
    for k in range(len(track.stamps)):
        for child, dropped in (('sonar_port', drop_port_samples),
                               ('sonar_stbd', drop_stbd_samples)):
            if k not in dropped:
                rec = track.stamps[k] + int(tf_delay_s * _NS)
                entries.append((rec, 0, k, '/tf',
                                track.tf_msg(k, child=child)))
    for n, (port_ns, stbd_ns) in enumerate(pings):
        port = _sonar_msg(port_ns, n, 40)
        port.header.frame_id = 'sonar_port'
        stbd = _sonar_msg(stbd_ns, n + 500, 40)
        stbd.header.frame_id = 'sonar_stbd'
        rec = max(port_ns, stbd_ns) + int(ping_lag_s * _NS)
        entries.append((rec, 2, n, PORT_TOPIC, port))
        entries.append((rec, 3, n, STBD_TOPIC, stbd))
    entries.sort(key=lambda e: e[:3])
    return [(e[3], e[4], e[0]) for e in entries]


def single_ping_messages(stamp_ns, *, span_ns=400_000_000):
    """
    Return the messages of one ping pair stamped exactly ``stamp_ns``.

    Two /tf samples ``span_ns`` either side of the stamp (the same pose, so
    any lookup time between them gives it), then the port and starboard
    messages.
    """
    return [
        ('/tf', _tf_msg(stamp_ns - span_ns, 0.0), stamp_ns - span_ns),
        ('/tf', _tf_msg(stamp_ns + span_ns, 0.0), stamp_ns + span_ns + 1),
        (PORT_TOPIC, _sonar_msg(stamp_ns, 0, 40), stamp_ns + span_ns + 2),
        (STBD_TOPIC, _sonar_msg(stamp_ns, 500, 40), stamp_ns + span_ns + 3),
    ]
