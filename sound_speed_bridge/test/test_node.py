"""
Node-level tests for the raw sentence passthrough publisher.

The ``raw`` topic exists so deployment bags capture the exact serial wire
traffic — including parse failures, the key diagnostic case — making
post-hoc RCA of baud/framing problems possible without a live serial
capture. These tests exercise
:meth:`SoundSpeedBridgeNode._handle_reading` directly with mocked serial
I/O, following the ``zda_serial_bridge/test/test_node.py`` pattern.
"""

from unittest.mock import MagicMock, patch

import pytest
import rclpy
from sound_speed_bridge.node import SoundSpeedBridgeNode
from sound_speed_bridge.parsers import SoundSpeedReading


@pytest.fixture(autouse=True)
def _ros_context():
    rclpy.init()
    yield
    rclpy.shutdown()


def _make_node(mock_serial_cls) -> SoundSpeedBridgeNode:
    """
    Build a real node with the serial port mocked out.

    The node's background serial thread starts in ``__init__``; the mocked
    port's ``read`` must return ``b''`` so that thread idles instead of
    feeding a MagicMock into ``parser.feed()``.
    """
    port = MagicMock()
    port.read.return_value = b''
    mock_serial_cls.return_value.__enter__.return_value = port
    return SoundSpeedBridgeNode()


@patch('sound_speed_bridge.node.serial.Serial')
def test_raw_publishes_on_parse_success(mock_serial_cls):
    """A parsed reading publishes its exact framed bytes on the raw topic."""
    node = _make_node(mock_serial_cls)
    try:
        node._raw_pub = MagicMock()
        reading = SoundSpeedReading(
            sound_speed_m_s=1500.123,
            raw_mm_s=1500123,
            raw_bytes=b'1500.123\r',
            receive_time_ns=1_700_000_000_000_000_000,
        )
        node._handle_reading(reading)
        assert node._raw_pub.publish.call_count == 1
        msg = node._raw_pub.publish.call_args.args[0]
        assert bytes(msg.data) == b'1500.123\r'
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_raw_publishes_on_parse_failure(mock_serial_cls):
    """
    A NaN (unparseable) reading still publishes its raw bytes verbatim.

    Parse failure is the key diagnostic case — the raw topic must carry
    the garbage that failed to parse, terminator included.
    """
    node = _make_node(mock_serial_cls)
    try:
        node._raw_pub = MagicMock()
        reading = SoundSpeedReading(
            sound_speed_m_s=float('nan'),
            raw_mm_s=None,
            raw_bytes=b'GARBAGE\r',
            receive_time_ns=1_700_000_000_000_000_000,
        )
        node._handle_reading(reading)
        assert node._raw_pub.publish.call_count == 1
        msg = node._raw_pub.publish.call_args.args[0]
        assert bytes(msg.data) == b'GARBAGE\r'
        assert node._parse_error_count == 1
    finally:
        node.destroy_node()
