"""
Tests for sensor_msgs/BatteryState extractor.

Construct BatteryState messages directly and exercise the extractor's
flatten contract — no rosbag2 round-trip needed for this layer.
"""

import math

from bag_analysis.extractors.battery import extract
from sensor_msgs.msg import BatteryState


def _build_battery_state() -> BatteryState:
    msg = BatteryState()
    msg.header.frame_id = 'battery_frame'
    msg.header.stamp.sec = 1_234_567_890
    msg.header.stamp.nanosec = 100_000_000
    msg.voltage = 24.5
    msg.current = -3.2
    msg.percentage = 0.78
    msg.cell_voltage = [4.05, 4.06, 4.07, 4.04, 4.05, 4.06]
    msg.location = 'main'
    msg.serial_number = 'BAT-1'
    return msg


def test_battery_extract_pulls_scalar_fields():
    fields = extract(_build_battery_state())
    # BatteryState floats are float32 in the .msg definition; round-trip
    # through the message class loses precision below ~1e-6, so a tight
    # default rel_tol fails. Loosen the tolerance to that of float32.
    assert math.isclose(fields['voltage'], 24.5, rel_tol=1e-6)
    assert math.isclose(fields['current'], -3.2, rel_tol=1e-6)
    assert math.isclose(fields['percentage'], 0.78, rel_tol=1e-6)
    assert fields['location'] == 'main'
    assert fields['serial_number'] == 'BAT-1'


def test_battery_extract_summarizes_cell_voltage_array():
    fields = extract(_build_battery_state())
    assert fields['n_cells'] == 6
    assert math.isclose(fields['cell_voltage_min'], 4.04, rel_tol=1e-6)
    assert math.isclose(fields['cell_voltage_max'], 4.07, rel_tol=1e-6)


def test_battery_extract_handles_empty_cell_voltage():
    msg = BatteryState()
    msg.cell_voltage = []
    fields = extract(msg)
    assert fields['n_cells'] == 0
    assert math.isnan(fields['cell_voltage_min'])
    assert math.isnan(fields['cell_voltage_max'])


def test_battery_extract_includes_header_t_ns():
    fields = extract(_build_battery_state())
    expected_ns = 1_234_567_890 * 1_000_000_000 + 100_000_000
    assert fields['header_t_ns'] == expected_ns
    assert fields['frame_id'] == 'battery_frame'
