# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Unit tests for the pure SonarDetections builder (marine_tools#82, #83).

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
    msg = detections_from_parsed(_parsed(), 'm3', TimeMsg(sec=100))
    assert len(msg.ping_info.rx_beamwidths) == 0
    assert len(msg.ping_info.tx_beamwidths) == 0
    # Unknown models likewise.
    msg = detections_from_parsed(_parsed(model=2040), 'm3', TimeMsg())
    assert len(msg.ping_info.rx_beamwidths) == 0
    assert len(msg.ping_info.tx_beamwidths) == 0


def test_beamwidth_arrays_track_published_beams_when_characterised(
        monkeypatch):
    # If a sourced figure is ever added to the table, the arrays must be one
    # element per published beam -- built by appending in the publish loop,
    # alongside every sibling per-beam array -- and must carry the table's
    # radians value unmodified (no hidden degree conversion).
    from kongsberg_em_bridge import node as node_mod
    monkeypatch.setitem(node_mod._RX_BEAMWIDTH_RAD, 30, 0.0175)
    monkeypatch.setitem(node_mod._TX_BEAMWIDTH_RAD, 30, 0.035)
    msg = detections_from_parsed(_parsed(), 'm3', TimeMsg())
    n = len(msg.two_way_travel_times)
    assert n == 3                       # every beam published, invalid too
    assert len(msg.ping_info.rx_beamwidths) == n
    assert len(msg.ping_info.tx_beamwidths) == n
    assert abs(msg.ping_info.rx_beamwidths[0] - 0.0175) < 1e-6
    assert abs(msg.ping_info.tx_beamwidths[0] - 0.035) < 1e-6


def test_one_sided_table_populates_only_that_array(monkeypatch):
    # PingInfo.msg declares each beamwidth array independently optional, so a
    # table with only an rx figure must yield rx per beam and tx EMPTY -- not
    # zero-filled, not mirrored.
    from kongsberg_em_bridge import node as node_mod
    monkeypatch.setitem(node_mod._RX_BEAMWIDTH_RAD, 30, 0.0175)
    msg = detections_from_parsed(_parsed(), 'm3', TimeMsg())
    assert len(msg.ping_info.rx_beamwidths) == len(msg.two_way_travel_times)
    assert len(msg.ping_info.tx_beamwidths) == 0


def test_non_positive_table_values_are_treated_as_unavailable(monkeypatch):
    # A placeholder 0.0 or a negative left in the table must not ship: the
    # README promises the fields are never zero-filled.
    from kongsberg_em_bridge import node as node_mod
    monkeypatch.setitem(node_mod._RX_BEAMWIDTH_RAD, 30, 0.0)
    monkeypatch.setitem(node_mod._TX_BEAMWIDTH_RAD, 30, -0.02)
    assert _resolve_beamwidths(30) == (None, None)
    msg = detections_from_parsed(_parsed(), 'm3', TimeMsg())
    assert len(msg.ping_info.rx_beamwidths) == 0
    assert len(msg.ping_info.tx_beamwidths) == 0


def test_no_sectors_falls_back_without_dropping_beams():
    # Corrupt/odd input: no TX sectors at all. Frequency reads 0.0, the
    # sector-derived per-beam fields fall back to 0, and every beam is still
    # published with the arrays aligned.
    parsed = _parsed()
    parsed['sectors'] = []
    msg = detections_from_parsed(parsed, 'm3', TimeMsg())
    assert msg.ping_info.frequency == 0.0
    assert len(msg.flags) == 3
    assert list(msg.tx_delays) == [0.0] * 3
    assert list(msg.tx_angles) == [0.0] * 3
    assert len(msg.rx_angles) == 3


def test_out_of_range_tx_sector_falls_back_to_sector_zero():
    # A beam naming a sector the ping does not carry takes sector 0's tilt and
    # delay rather than raising or being dropped.
    beams = [_beam(sector=0), _beam(sector=7)]
    msg = detections_from_parsed(_parsed(beams=beams), 'm3', TimeMsg())
    assert len(msg.flags) == 2
    assert msg.tx_delays[1] == msg.tx_delays[0]
    assert msg.tx_angles[1] == msg.tx_angles[0]


def test_ping_info_basics():
    msg = detections_from_parsed(_parsed(), 'm3', TimeMsg(sec=7))
    assert msg.header.frame_id == 'm3'
    assert msg.header.stamp.sec == 7
    assert abs(msg.ping_info.sound_speed - 1500.0) < 1e-3
    assert abs(msg.ping_info.frequency - 500000.0) < 1e-3


def _per_beam_arrays(msg):
    """Every array in the message that carries one element per beam."""
    return {
        'flags': msg.flags,
        'two_way_travel_times': msg.two_way_travel_times,
        'tx_delays': msg.tx_delays,
        'intensities': msg.intensities,
        'tx_angles': msg.tx_angles,
        'rx_angles': msg.rx_angles,
    }


def test_invalid_beam_is_published_with_bad_sonar_flag():
    # marine_tools#83: an invalid beam must be reported, not destroyed. It
    # was previously dropped, which made the flag field decorative and left
    # the bag -- the data of record -- unable to say the beam ever existed.
    from marine_acoustic_msgs.msg import DetectionFlag
    beams = [_beam(), _beam(valid=False, twtt=0.0), _beam()]
    msg = detections_from_parsed(_parsed(beams=beams), 'm3', TimeMsg())
    assert len(msg.flags) == 3
    assert [f.flag for f in msg.flags] == [
        DetectionFlag.DETECT_OK,
        DetectionFlag.DETECT_BAD_SONAR,
        DetectionFlag.DETECT_OK,
    ]
    # The invalid beam is there, carrying the travel time the sonar gave it.
    assert msg.two_way_travel_times[1] == 0.0


def test_per_beam_arrays_stay_aligned():
    # The arrays are built in one loop and must not diverge -- checked over
    # a mix of valid and invalid beams, and over an all-invalid ping.
    for beams in ([_beam(), _beam(valid=False, twtt=0.0), _beam(), _beam()],
                  [_beam(valid=False, twtt=0.0)] * 3,
                  [_beam()] * 2):
        msg = detections_from_parsed(_parsed(beams=beams), 'm3', TimeMsg())
        lengths = {name: len(arr)
                   for name, arr in _per_beam_arrays(msg).items()}
        assert set(lengths.values()) == {len(beams)}, lengths


def test_per_beam_arrays_stay_aligned_with_beamwidths(monkeypatch):
    # Same invariant once the beamwidth arrays are populated: they are
    # per-beam too, and must match the rest element for element.
    from kongsberg_em_bridge import node as node_mod
    monkeypatch.setitem(node_mod._RX_BEAMWIDTH_RAD, 30, 0.0175)
    monkeypatch.setitem(node_mod._TX_BEAMWIDTH_RAD, 30, 0.035)
    beams = [_beam(), _beam(valid=False, twtt=0.0), _beam()]
    msg = detections_from_parsed(_parsed(beams=beams), 'm3', TimeMsg())
    arrays = _per_beam_arrays(msg)
    arrays['rx_beamwidths'] = msg.ping_info.rx_beamwidths
    arrays['tx_beamwidths'] = msg.ping_info.tx_beamwidths
    lengths = {name: len(arr) for name, arr in arrays.items()}
    assert set(lengths.values()) == {len(beams)}, lengths
