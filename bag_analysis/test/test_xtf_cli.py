"""
Tests for bag_analysis.cli.bag_to_xtf helpers.

Covers the timestamp and speed helpers. The pose lookup, which must never
fall back to the latest transform, is tested in ``test_tf_lookup.py`` and
``test_xtf_pose_time.py``.
"""

import datetime as _dt

from bag_analysis.cli.bag_to_xtf import (
    _DEFAULT_SECONDS_PER_PING,
    _parse_time,
    _PendingPing,
    _seconds_per_ping,
    _speed_mps,
    _stamp_ns,
)
from builtin_interfaces.msg import Time as TimeMsg
import numpy as np
import pytest


def test_stamp_ns():
    assert _stamp_ns(TimeMsg(sec=12, nanosec=500_000_000)) == 12_500_000_000


def test_parse_time_none():
    assert _parse_time(None) is None


def test_parse_time_epoch_seconds():
    assert _parse_time('100.5') == 100_500_000_000


def test_parse_time_iso_utc():
    # A naive ISO time is read as UTC, so it equals the explicit-Z form.
    expected = int(
        _dt.datetime(2026, 6, 15, 16, 26, 49,
                     tzinfo=_dt.timezone.utc).timestamp() * 1e9)
    assert _parse_time('2026-06-15T16:26:49') == expected
    assert _parse_time('2026-06-15T16:26:49Z') == expected


def test_speed_mps_from_ecef_displacement():
    a = _PendingPing(t_ns=0, ecef=np.array([0.0, 0.0, 0.0]))
    b = _PendingPing(t_ns=2_000_000_000, ecef=np.array([0.0, 0.0, 10.0]))
    assert _speed_mps(a, b) == 5.0  # 10 m over 2 s


def test_speed_mps_zero_dt():
    a = _PendingPing(t_ns=5, ecef=np.array([0.0, 0.0, 0.0]))
    b = _PendingPing(t_ns=5, ecef=np.array([1.0, 0.0, 0.0]))
    assert _speed_mps(a, b) == 0.0


def test_seconds_per_ping_first_ping_uses_default():
    # No previous ping -> nominal value, never 0 (PINGVerter rejects spp <= 0).
    cur = _PendingPing(t_ns=1_000_000_000)
    assert _seconds_per_ping(None, cur) == _DEFAULT_SECONDS_PER_PING
    assert _seconds_per_ping(None, cur) > 0.0


def test_seconds_per_ping_uses_interping_interval():
    prev = _PendingPing(t_ns=1_000_000_000)
    cur = _PendingPing(t_ns=1_050_000_000)  # 50 ms later
    assert _seconds_per_ping(prev, cur) == pytest.approx(0.05)


def test_seconds_per_ping_gap_falls_back_to_default():
    prev = _PendingPing(t_ns=1_000_000_000)
    cur = _PendingPing(t_ns=6_000_000_000)  # 5 s gap (dropout)
    assert _seconds_per_ping(prev, cur) == _DEFAULT_SECONDS_PER_PING


def test_seconds_per_ping_nonincreasing_stamp_falls_back_to_default():
    # Duplicate / out-of-order stamps must not yield 0 or a negative interval.
    prev = _PendingPing(t_ns=2_000_000_000)
    same = _PendingPing(t_ns=2_000_000_000)
    earlier = _PendingPing(t_ns=1_000_000_000)
    assert _seconds_per_ping(prev, same) == _DEFAULT_SECONDS_PER_PING
    assert _seconds_per_ping(prev, earlier) == _DEFAULT_SECONDS_PER_PING
