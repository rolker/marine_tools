"""
Node-level tests for the UTC-validity suppression gate.

The whole reason this node exists is to keep the M3 sonar (and any other
downstream sounder) from receiving a wrong wall-clock during cold-start.
That gate lives in :meth:`ZdaSerialBridgeNode._on_utc_time` and is
exercised here directly with mocked serial I/O.
"""

from unittest.mock import MagicMock, patch

from diagnostic_msgs.msg import DiagnosticStatus
import pytest
import rclpy

from sbg_driver.msg import SbgUtcTime
from zda_serial_bridge.node import ZdaSerialBridgeNode


@pytest.fixture(autouse=True)
def _ros_context():
    rclpy.init()
    yield
    rclpy.shutdown()


def _make_msg(clock_utc_status: int, clock_utc_sync: bool = True) -> SbgUtcTime:
    msg = SbgUtcTime()
    msg.year = 2026
    msg.month = 5
    msg.day = 1
    msg.hour = 16
    msg.min = 56
    msg.sec = 12
    msg.nanosec = 500_000_000
    msg.clock_status.clock_utc_status = clock_utc_status
    msg.clock_status.clock_utc_sync = clock_utc_sync
    return msg


@patch('zda_serial_bridge.node.serial.Serial')
def test_suppresses_below_min_utc_status(mock_serial_cls):
    """Default ``min_utc_status=2`` blocks status 0 and 1, allows status 2."""
    fake_port = MagicMock()
    mock_serial_cls.return_value = fake_port
    node = ZdaSerialBridgeNode()
    try:
        node._on_utc_time(_make_msg(clock_utc_status=0))
        assert node._write_count == 0
        assert node._suppressed_count == 1
        assert fake_port.write.call_count == 0

        node._on_utc_time(_make_msg(clock_utc_status=1))
        assert node._write_count == 0
        assert node._suppressed_count == 2
        assert fake_port.write.call_count == 0

        node._on_utc_time(_make_msg(clock_utc_status=2))
        assert node._write_count == 1
        assert node._suppressed_count == 2
        assert fake_port.write.call_count == 1
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_suppression_records_status_text(mock_serial_cls):
    """Suppressed messages set ``_last_status_text`` for diagnostics visibility."""
    fake_port = MagicMock()
    mock_serial_cls.return_value = fake_port
    node = ZdaSerialBridgeNode()
    try:
        node._on_utc_time(_make_msg(clock_utc_status=0))
        assert 'suppressed' in node._last_status_text
        assert 'clock_utc_status=0' in node._last_status_text
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_require_utc_sync_gate(mock_serial_cls):
    """When ``require_utc_sync`` is on, status>=threshold without sync suppresses."""
    fake_port = MagicMock()
    mock_serial_cls.return_value = fake_port
    node = ZdaSerialBridgeNode()
    try:
        node._require_utc_sync = True

        node._on_utc_time(_make_msg(clock_utc_status=2, clock_utc_sync=False))
        assert node._write_count == 0
        assert node._suppressed_count == 1
        assert 'clock_utc_sync=False' in node._last_status_text

        node._on_utc_time(_make_msg(clock_utc_status=2, clock_utc_sync=True))
        assert node._write_count == 1
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_emitted_payload_is_valid_zda(mock_serial_cls):
    """When the gate opens, the bytes written to the port are a valid $ZDA."""
    fake_port = MagicMock()
    mock_serial_cls.return_value = fake_port
    node = ZdaSerialBridgeNode()
    try:
        node._on_utc_time(_make_msg(clock_utc_status=2))
        assert fake_port.write.call_count == 1
        payload = fake_port.write.call_args.args[0]
        assert payload.startswith(b'$GPZDA,165612.50,01,05,2026,,*')
        assert payload.endswith(b'\r\n')
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_rejects_non_ascii_talker_id(mock_serial_cls):
    """Non-ASCII letters that pass ``isalpha()`` must fail at startup, not later."""
    fake_port = MagicMock()
    mock_serial_cls.return_value = fake_port
    rclpy.shutdown()
    rclpy.init(args=['--ros-args', '-p', 'talker_id:=ßZ'])
    with pytest.raises(ValueError, match='ASCII'):
        ZdaSerialBridgeNode()


# --------------------------------------------------------------- diagnostics


def _capture_diag(node) -> tuple[int, str]:
    """Trigger a diagnostic publish on ``node`` and return (level, msg_text)."""
    node._diag_pub = MagicMock()
    node._publish_diagnostics()
    diag_arr = node._diag_pub.publish.call_args.args[0]
    status = diag_arr.status[0]
    return status.level, status.message


@patch('zda_serial_bridge.node.serial.Serial')
def test_diagnostic_warmup_no_message_yet_is_ok(mock_serial_cls):
    """Cold start with no SbgUtcTime yet, within startup grace → OK."""
    mock_serial_cls.return_value = MagicMock()
    node = ZdaSerialBridgeNode()
    try:
        # Node just constructed, _last_msg_ns=None, _node_start_ns is now.
        # Within startup_grace_sec (default 5s) the diagnostic must be OK
        # with a "starting up" message — not an ERROR alarm.
        level, msg_text = _capture_diag(node)
        assert level == DiagnosticStatus.OK, (level, msg_text)
        assert 'starting up' in msg_text, msg_text
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_diagnostic_past_grace_no_message_is_error(mock_serial_cls):
    """No SbgUtcTime past startup grace → ERROR (real silence)."""
    mock_serial_cls.return_value = MagicMock()
    node = ZdaSerialBridgeNode()
    try:
        # Rewind _node_start_ns so we're past the grace window.
        node._node_start_ns -= int((node._startup_grace + 1) * 1e9)
        level, msg_text = _capture_diag(node)
        assert level == DiagnosticStatus.ERROR, (level, msg_text)
        assert 'No SbgUtcTime' in msg_text, msg_text
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_diagnostic_intentional_suppression_is_ok(mock_serial_cls):
    """Messages arriving but gated by min_utc_status → OK, not ERROR."""
    mock_serial_cls.return_value = MagicMock()
    node = ZdaSerialBridgeNode()
    try:
        # Past the startup grace so we're not relying on the warmup OK
        # path — the suppression gate must produce OK on its own merits.
        node._node_start_ns -= int((node._startup_grace + 1) * 1e9)
        # Suppressed messages: messages arriving (so last_msg_age is not
        # None), but emission gated → _last_emit_ns stays None and
        # _last_status_text starts with "suppressed".
        node._on_utc_time(_make_msg(clock_utc_status=0))
        assert node._last_emit_ns is None
        assert node._last_status_text.startswith('suppressed')
        level, msg_text = _capture_diag(node)
        assert level == DiagnosticStatus.OK, (level, msg_text)
        assert 'output gated' in msg_text, msg_text
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_diagnostic_recovers_to_ok_after_first_emit(mock_serial_cls):
    """Once the gate opens and an emit lands, diagnostic should be OK."""
    mock_serial_cls.return_value = MagicMock()
    node = ZdaSerialBridgeNode()
    try:
        node._on_utc_time(_make_msg(clock_utc_status=2))
        assert node._last_emit_ns is not None
        level, msg_text = _capture_diag(node)
        assert level == DiagnosticStatus.OK, (level, msg_text)
        assert msg_text == 'OK', msg_text
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_diagnostic_stale_after_emit_then_silence(mock_serial_cls):
    """Was emitting, then SbgUtcTime stops past error threshold → ERROR."""
    mock_serial_cls.return_value = MagicMock()
    node = ZdaSerialBridgeNode()
    try:
        node._on_utc_time(_make_msg(clock_utc_status=2))
        # Rewind both timestamps to past the error threshold.
        elapsed = int((node._stale_error + 1) * 1e9)
        node._last_msg_ns -= elapsed
        node._last_emit_ns -= elapsed
        level, msg_text = _capture_diag(node)
        assert level == DiagnosticStatus.ERROR, (level, msg_text)
        assert 'No SbgUtcTime for' in msg_text, msg_text
    finally:
        node.destroy_node()
