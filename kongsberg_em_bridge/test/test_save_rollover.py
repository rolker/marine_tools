# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Unit tests for the ``.all`` recording rollover prototype (marine_tools).

Covers the pure roll-decision (``save_rollover_due``) and the collision-safe
segment-path helper (``KongsbergEmBridge._unique_all_path``). Both are exercised
without an rclpy node so the test needs no ROS graph.
"""

import os

from kongsberg_em_bridge.node import KongsbergEmBridge, save_rollover_due


def test_disabled_by_default_never_rolls():
    # 0/0 limits: neither trigger arms regardless of elapsed/bytes.
    assert save_rollover_due(10_000.0, 10_000_000_000, 0.0, 0) == (False, '')


def test_time_trigger():
    assert save_rollover_due(600.0, 0, 600.0, 0) == (True, 'time')
    assert save_rollover_due(599.9, 0, 600.0, 0) == (False, '')


def test_size_trigger():
    assert save_rollover_due(0.0, 1_000_000_000, 0.0, 1_000_000_000) == (True, 'size')
    assert save_rollover_due(0.0, 999_999_999, 0.0, 1_000_000_000) == (False, '')


def test_time_reported_first_when_both_fire():
    assert save_rollover_due(700.0, 2_000, 600.0, 1_000) == (True, 'time')


def test_unknown_start_time_inert_for_time_trigger():
    # elapsed None (open time not yet known) must not roll on the time trigger;
    # the size trigger still works.
    assert save_rollover_due(None, 0, 600.0, 0) == (False, '')
    assert save_rollover_due(None, 5_000, 600.0, 1_000) == (True, 'size')


def test_unique_all_path_plain_when_absent(tmp_path):
    path = KongsbergEmBridge._unique_all_path(str(tmp_path))
    assert path.endswith('.all')
    assert os.path.dirname(path) == str(tmp_path)
    assert not os.path.exists(path)


def test_unique_all_path_disambiguates_same_second(tmp_path):
    # Simulate a size-triggered roll inside the same wall-clock second: the
    # base name already exists, so a _NN suffix must be chosen rather than
    # returning a path that 'wb' would truncate.
    first = KongsbergEmBridge._unique_all_path(str(tmp_path))
    open(first, 'wb').close()
    second = KongsbergEmBridge._unique_all_path(str(tmp_path))
    assert second != first
    assert not os.path.exists(second)
