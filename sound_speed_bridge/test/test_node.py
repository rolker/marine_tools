"""
Node-level tests for the raw sentence passthrough publisher.

The ``raw`` topic exists so deployment bags capture the bytes of each
framed serial sentence — including sentences that fail to parse, the key
diagnostic case — making post-hoc RCA of garbled traffic possible without
a live serial capture. It is a per-sentence passthrough, not a wire tap:
inter-sentence padding is stripped by the parser and a stream that never
frames (e.g. wrong baud) publishes nothing (see rolker/marine_tools#77).
These tests exercise
:meth:`SoundSpeedBridgeNode._handle_reading` directly with mocked serial
I/O, following the ``zda_serial_bridge/test/test_node.py`` pattern.
"""

from unittest.mock import MagicMock, patch

import pytest
import rclpy
from sound_speed_bridge.node import SoundSpeedBridgeNode
from sound_speed_bridge.parsers import SoundSpeedReading
from std_msgs.msg import UInt8MultiArray


@pytest.fixture(autouse=True)
def _ros_context():
    rclpy.init()
    yield
    rclpy.shutdown()


def _make_node(mock_serial_cls) -> SoundSpeedBridgeNode:
    """
    Build a real node with the serial port mocked out and its serial thread stopped.

    The node's background serial thread starts in ``__init__``. The mocked
    port's ``read`` returns ``b''`` so the loop never feeds a MagicMock into
    ``parser.feed()``, but ``b''`` also returns instantly — leaving the thread
    running would busy-spin at full CPU for the node's lifetime. These tests
    drive :meth:`SoundSpeedBridgeNode._handle_reading` directly and never need
    the thread, so it is stopped deterministically right after construction.
    """
    port = MagicMock()
    port.read.return_value = b''
    mock_serial_cls.return_value.__enter__.return_value = port
    node = SoundSpeedBridgeNode()
    try:
        node._stop_event.set()
        node._serial_thread.join(timeout=2.0)
        assert not node._serial_thread.is_alive()
        # The thread has exited its loop, so clearing the event cannot revive
        # it — but it must be cleared for _handle_reading's shutdown guard to
        # let the direct-call tests through.
        node._stop_event.clear()
        # External contract with unh_echoboats_project11#396's record list: the
        # topic must stay a bare relative `raw` of type UInt8MultiArray. Checked
        # on the real publisher, before any test swaps in a mock.
        assert node._raw_pub.topic_name == '/raw'
        assert node._raw_pub.msg_type is UInt8MultiArray
    except BaseException:
        node.destroy_node()
        raise
    return node


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
        assert node._parse_error_count == 0
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


@patch('sound_speed_bridge.node.serial.Serial')
def test_handle_reading_noop_after_stop(mock_serial_cls):
    """
    A reading delivered after the stop event is set publishes nothing.

    destroy_node()'s 2 s join is best-effort, so a wedged serial thread may
    deliver one last reading after the publishers are destroyed — the
    shutdown guard in _handle_reading must swallow it.
    """
    node = _make_node(mock_serial_cls)
    try:
        node._raw_pub = MagicMock()
        node._pub = MagicMock()
        node._stop_event.set()
        reading = SoundSpeedReading(
            sound_speed_m_s=1500.123,
            raw_mm_s=1500123,
            raw_bytes=b'1500.123\r',
            receive_time_ns=1_700_000_000_000_000_000,
        )
        node._handle_reading(reading)
        assert node._raw_pub.publish.call_count == 0
        assert node._pub.publish.call_count == 0
    finally:
        node._stop_event.clear()
        node.destroy_node()
