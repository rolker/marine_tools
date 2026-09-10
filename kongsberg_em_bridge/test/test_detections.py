# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Unit tests for the pure SonarDetections builder (marine_tools#82).

``detections_from_parsed`` / ``_resolve_beamwidths`` are module-level pure
functions, so -- like ``test_sonar_info.py`` -- these tests need no rclpy node
or executor, only the generated message classes.
"""

from builtin_interfaces.msg import Time as TimeMsg
from kongsberg_em_bridge.node import (_resolve_beamwidths,
                                      detections_from_parsed)


def _beam(valid=True, twtt=0.1, angle=10.0, sector=0):
    return {'valid': valid, 'twtt': twtt, 'pointing_angle_deg': angle,
            'reflectivity_db': -20.0, 'tx_sector': sector}


def _parsed(model=30, beams=None):
    """Minimal parsed-N/78 dict with the keys the builder consumes."""
    if beams is None:
        beams = [_beam(), _beam(valid=False, twtt=0.0), _beam()]
    return {
        'model': model,
        'sound_speed': 1500.0,
        'sectors': [{'centre_frequency': 500000.0, 'tilt_deg': 0.0,
                     'tx_delay': 0.001}],
        'beams': beams,
    }


def test_resolve_beamwidths_m3_uncharacterised():
    # Model 30 is the M3: mapped explicitly, deliberately uncharacterised.
    assert _resolve_beamwidths(30) == (None, None)


def test_resolve_beamwidths_unknown_model():
    # An unmapped model number must not borrow another device's figure.
    assert _resolve_beamwidths(2040) == (None, None)


def test_publish_leaves_beamwidths_empty_for_uncharacterised_device():
    # The issue's explicit acceptance item: an uncharacterised device leaves
    # the fields empty, so the consumer takes its documented fallback.
    msg = detections_from_parsed(_parsed(), 'm3', TimeMsg(sec=100),
                                 skip_invalid=True)
    assert len(msg.ping_info.rx_beamwidths) == 0
    assert len(msg.ping_info.tx_beamwidths) == 0
    # Unknown models likewise.
    msg = detections_from_parsed(_parsed(model=2040), 'm3', TimeMsg(),
                                 skip_invalid=True)
    assert len(msg.ping_info.rx_beamwidths) == 0
    assert len(msg.ping_info.tx_beamwidths) == 0


def test_beamwidth_arrays_track_published_beams_when_characterised(
        monkeypatch):
    # If a sourced figure is ever added to the table, the arrays must be one
    # element per PUBLISHED beam -- built by appending in the publish loop,
    # never sized from the raw pre-filter beam count -- and must carry the
    # table's radians value unmodified (no hidden degree conversion).
    from kongsberg_em_bridge import node as node_mod
    monkeypatch.setitem(node_mod._RX_BEAMWIDTH_RAD, 30, 0.0175)
    monkeypatch.setitem(node_mod._TX_BEAMWIDTH_RAD, 30, 0.035)
    msg = detections_from_parsed(_parsed(), 'm3', TimeMsg(),
                                 skip_invalid=True)
    n = len(msg.two_way_travel_times)
    assert n == 2                       # the invalid beam was filtered out
    assert len(msg.ping_info.rx_beamwidths) == n
    assert len(msg.ping_info.tx_beamwidths) == n
    assert abs(msg.ping_info.rx_beamwidths[0] - 0.0175) < 1e-6
    assert abs(msg.ping_info.tx_beamwidths[0] - 0.035) < 1e-6


def test_ping_info_basics():
    msg = detections_from_parsed(_parsed(), 'm3', TimeMsg(sec=7),
                                 skip_invalid=True)
    assert msg.header.frame_id == 'm3'
    assert msg.header.stamp.sec == 7
    assert abs(msg.ping_info.sound_speed - 1500.0) < 1e-3
    assert abs(msg.ping_info.frequency - 500000.0) < 1e-3
