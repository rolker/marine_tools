"""
Synthetic bag streams for the bag_to_xtf tests.

``fake_messages`` builds the ``(topic, msg, t_ns)`` tuples that
``bag_analysis.reader.iter_messages`` would yield for a short sidescan
run: one ``earth -> sonar`` transform per ping, a port and a starboard
``RawSonarImage``-shaped message, and a nadir range. The sensor moves
along the ground at ``speed_mps``, so speed and seconds-per-ping are
non-trivial and a split that resets the carry-over changes the bytes.
"""

from types import SimpleNamespace

from bag_analysis.xtf.geo import geodetic_to_ecef
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
