# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Unit tests for the pure SonarInfo helpers (marine_tools#69 / ADR-0009).

``sonar_info_from_parsed`` / ``acquisition_signature`` are module-level pure
functions, so -- like ``test_save_rollover.py`` -- these tests need no rclpy
node or executor, only the generated message class.
"""

import math

from builtin_interfaces.msg import Time as TimeMsg
from kongsberg_em_bridge.node import (acquisition_signature,
                                      sonar_info_from_parsed,
                                      sonar_model_name)
from marine_interfaces.msg import SonarInfo


def _parsed(model=30, sectors=None):
    """Minimal parsed-N/78 dict with the keys the helpers consume."""
    if sectors is None:
        sectors = [{'signal_length': 0.0001, 'waveform': 0,
                    'bandwidth': 12000.0, 'tilt_deg': 0.0,
                    'tx_delay': 0.001, 'centre_frequency': 500000.0}]
    return {'model': model, 'sectors': sectors}


def test_sonar_model_name():
    assert sonar_model_name(30) == 'kongsberg-m3'
    assert sonar_model_name(2040) == 'kongsberg-em2040'


def test_sonar_info_acquisition_block():
    msg = sonar_info_from_parsed(_parsed(), 'm3', TimeMsg(sec=100))
    assert msg.header.frame_id == 'm3'
    assert msg.header.stamp.sec == 100
    assert msg.sonar_model == 'kongsberg-m3'
    # Parallel arrays: equal length, one element per TX sector.
    assert len(msg.pulse_lengths) == len(msg.bandwidths) \
        == len(msg.tx_signal_types) == 1
    assert abs(msg.pulse_lengths[0] - 0.0001) < 1e-9
    assert abs(msg.bandwidths[0] - 12000.0) < 1e-3
    assert msg.tx_signal_types[0] == SonarInfo.SIGNAL_TYPE_CW


def test_sonar_info_signal_type_mapping():
    for waveform, expected in [
            (0, SonarInfo.SIGNAL_TYPE_CW),
            (1, SonarInfo.SIGNAL_TYPE_FM_UP),
            (2, SonarInfo.SIGNAL_TYPE_FM_DOWN),
            (7, SonarInfo.SIGNAL_TYPE_UNKNOWN)]:  # unmapped id stays honest
        sectors = [{'signal_length': 0.001, 'waveform': waveform,
                    'bandwidth': 1000.0}]
        msg = sonar_info_from_parsed(_parsed(sectors=sectors), 'm3', TimeMsg())
        assert msg.tx_signal_types[0] == expected


def test_sonar_info_intensity_semantics_and_sentinels():
    msg = sonar_info_from_parsed(_parsed(), 'm3', TimeMsg())
    # intensities are reflectivity in dB: relative, uncalibrated power ratio,
    # published as already-physical floats (identity raw->physical map).
    assert msg.intensity_quantity == SonarInfo.QUANTITY_POWER
    assert msg.intensity_scale == SonarInfo.INTENSITY_SCALE_DB
    assert msg.intensity_reference == SonarInfo.REFERENCE_UNCALIBRATED_RELATIVE
    assert msg.scale == 1.0 and msg.offset == 0.0
    # Honest unknowns: NaN sentinels must be SET (rosidl defaults are 0.0,
    # which would silently claim real dB values), enums at *_UNKNOWN.
    assert msg.tvg_model == SonarInfo.TVG_UNKNOWN
    assert math.isnan(msg.tvg_absorption_db_per_km)
    assert math.isnan(msg.source_level_db)
    assert msg.angular_normalization == SonarInfo.ANGULAR_NORMALIZATION_UNKNOWN
    # Calibration + curve fields stay at their documented "absent" encodings.
    assert msg.calibration_ref == ''
    assert len(msg.angular_response_angle_deg) == 0
    assert len(msg.beam_pattern_angle_deg) == 0


def test_acquisition_signature_change_detection():
    base = _parsed()
    assert acquisition_signature(base) == acquisition_signature(_parsed())
    for key, value in [('signal_length', 0.0002), ('waveform', 1),
                       ('bandwidth', 30000.0)]:
        changed = _parsed(sectors=[dict(base['sectors'][0], **{key: value})])
        assert acquisition_signature(changed) != acquisition_signature(base)
    assert acquisition_signature(_parsed(model=2040)) \
        != acquisition_signature(base)
    # Sector count changes (multi-sector sonar) also re-publish.
    two = _parsed(sectors=base['sectors'] * 2)
    assert acquisition_signature(two) != acquisition_signature(base)
