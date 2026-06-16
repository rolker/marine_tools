"""
Tests for bag_analysis.cli.bag_to_xtf helpers.

Covers the timestamp/speed helpers and the bounded TF lookup, which is
the safety-critical piece: a fallback to the latest transform must be
age-bounded so a TF gap can't silently stamp a ping with an unrelated
pose.
"""

from bag_analysis.cli.bag_to_xtf import (
    _lookup_pose,
    _PendingPing,
    _speed_mps,
    _stamp_ns,
)
from builtin_interfaces.msg import Time as TimeMsg
from geometry_msgs.msg import TransformStamped
import numpy as np
from rclpy.duration import Duration
from tf2_ros import Buffer

_MAX_AGE_NS = 1_000_000_000  # 1 s


def test_stamp_ns():
    assert _stamp_ns(TimeMsg(sec=12, nanosec=500_000_000)) == 12_500_000_000


def test_speed_mps_from_ecef_displacement():
    a = _PendingPing(t_ns=0, ecef=np.array([0.0, 0.0, 0.0]))
    b = _PendingPing(t_ns=2_000_000_000, ecef=np.array([0.0, 0.0, 10.0]))
    assert _speed_mps(a, b) == 5.0  # 10 m over 2 s


def test_speed_mps_zero_dt():
    a = _PendingPing(t_ns=5, ecef=np.array([0.0, 0.0, 0.0]))
    b = _PendingPing(t_ns=5, ecef=np.array([1.0, 0.0, 0.0]))
    assert _speed_mps(a, b) == 0.0


def _tf(frame: str, sec: int, x: float, y: float, z: float) -> TransformStamped:
    tf = TransformStamped()
    tf.header.stamp = TimeMsg(sec=sec, nanosec=0)
    tf.header.frame_id = 'earth'
    tf.child_frame_id = frame
    tf.transform.translation.x = x
    tf.transform.translation.y = y
    tf.transform.translation.z = z
    tf.transform.rotation.w = 1.0
    return tf


def _buffer() -> Buffer:
    buf = Buffer(cache_time=Duration(seconds=600))
    buf.set_transform(_tf('sensor', 10, 1.0, 2.0, 3.0), 'test')
    return buf


def test_lookup_pose_exact():
    pose, status = _lookup_pose(
        _buffer(), 'sensor', 'earth', TimeMsg(sec=10, nanosec=0), _MAX_AGE_NS)
    assert status == 'exact'
    assert pose[0] == (1.0, 2.0, 3.0)


def test_lookup_pose_approx_within_age():
    pose, status = _lookup_pose(
        _buffer(), 'sensor', 'earth',
        TimeMsg(sec=10, nanosec=500_000_000), _MAX_AGE_NS)
    assert status == 'approx'
    assert pose is not None


def test_lookup_pose_stale_beyond_age():
    pose, status = _lookup_pose(
        _buffer(), 'sensor', 'earth', TimeMsg(sec=20, nanosec=0), _MAX_AGE_NS)
    assert status == 'stale'
    assert pose is None


def test_lookup_pose_missing_frame():
    pose, status = _lookup_pose(
        _buffer(), 'ghost', 'earth', TimeMsg(sec=10, nanosec=0), _MAX_AGE_NS)
    assert status == 'missing'
    assert pose is None
