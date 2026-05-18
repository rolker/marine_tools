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
import serial
from zda_serial_bridge.node import (
    validate_talker_id, validate_timing_params, ZdaSerialBridgeNode,
)


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


@pytest.mark.parametrize(
    'bad_talker_id', ['ßZ', 'g1', 'GPS', '', 'g', 'G P', '#A'],
)
def test_rejects_invalid_talker_id(bad_talker_id):
    """validate_talker_id rejects non-ASCII, non-alpha, and wrong-length input."""
    # The autouse ``_ros_context`` fixture still runs around this test
    # (and the other pure-validator tests below), but the test itself
    # exercises the module-level validator directly and doesn't touch
    # rclpy state — so the rclpy.init/shutdown bracketing is harmless
    # overhead, not a dependency.
    with pytest.raises(ValueError, match='ASCII'):
        validate_talker_id(bad_talker_id)


def test_validate_talker_id_normalises_case():
    """Lowercase / mixed-case talker IDs are upper-cased on the way through."""
    assert validate_talker_id('gp') == 'GP'
    assert validate_talker_id('Gp') == 'GP'
    assert validate_talker_id('GP') == 'GP'


# --------------------------------------------------- timing-param validation


@pytest.mark.parametrize('warn,err', [(10.0, 5.0), (1.0, 0.5), (3.1, 3.0)])
def test_rejects_inverted_stale_ages(warn, err):
    """Warn > error makes the WARN branch unreachable — must raise."""
    with pytest.raises(ValueError, match='WARN branch'):
        validate_timing_params(warn, err, 5.0, 2.0)


@pytest.mark.parametrize('field,value', [
    ('stale_age_warn_sec', -1.0),
    ('stale_age_error_sec', -0.1),
    ('startup_grace_sec', -2.0),
    ('reconnect_delay_sec', -0.5),
])
def test_rejects_negative_timing_params(field, value):
    """Negative durations are nonsense — must raise loudly at construction."""
    kwargs = {
        'stale_age_warn_sec': 2.0,
        'stale_age_error_sec': 10.0,
        'startup_grace_sec': 5.0,
        'reconnect_delay_sec': 2.0,
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match='must be >= 0'):
        validate_timing_params(**kwargs)


def test_accepts_valid_timing_params():
    """Defaults and the zero-everything corner case both pass."""
    validate_timing_params(2.5, 10.0, 5.0, 2.0)
    validate_timing_params(0.0, 0.0, 0.0, 0.0)
    validate_timing_params(5.0, 5.0, 0.0, 0.0)  # warn == error is fine


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
def test_diagnostic_gate_state_decoupled_from_status_text(mock_serial_cls):
    """Diagnostic level must come from gate_state, not _last_status_text wording."""
    mock_serial_cls.return_value = MagicMock()
    node = ZdaSerialBridgeNode()
    try:
        node._node_start_ns -= int((node._startup_grace + 1) * 1e9)
        node._on_utc_time(_make_msg(clock_utc_status=0))
        assert node._gate_state == 'suppressed_status'
        # Simulate future code editing the human-readable text. As
        # long as gate_state stays 'suppressed_*', diagnostic stays OK.
        node._last_status_text = 'gated by clock_utc_status (translated label)'
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
def test_diagnostic_regate_after_emit_is_warn(mock_serial_cls):
    """Re-gate after successful emit (clock degraded) → WARN, not 'stale'."""
    mock_serial_cls.return_value = MagicMock()
    node = ZdaSerialBridgeNode()
    try:
        # Open the gate and emit once.
        node._on_utc_time(_make_msg(clock_utc_status=2))
        assert node._last_emit_ns is not None

        # Clock degrades back to status 1 → gate suppresses.
        node._on_utc_time(_make_msg(clock_utc_status=1))
        assert node._gate_state == 'suppressed_status'

        # Push _last_emit_ns past stale_warn but not stale_error so the
        # bug-fix branch (re-gated WARN) is the one under test rather
        # than the OK pre-stale branch.
        elapsed = int((node._stale_warn + 0.5) * 1e9)
        node._last_emit_ns -= elapsed
        level, msg_text = _capture_diag(node)
        assert level == DiagnosticStatus.WARN, (level, msg_text)
        assert 're-gated' in msg_text, msg_text
        # Must NOT report as 'stale' — that wording belongs to
        # transport-failure cases.
        assert 'stale' not in msg_text.lower(), msg_text
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_diagnostic_msg_stale_during_gating_surfaces_as_warn(mock_serial_cls):
    """Slow upstream msgs during gating surface as WARN, not OK 'output gated'."""
    mock_serial_cls.return_value = MagicMock()
    node = ZdaSerialBridgeNode()
    try:
        # Receive a single suppressed message so gate is gated but
        # _last_emit_ns stays None (gate never opened).
        node._on_utc_time(_make_msg(clock_utc_status=1))
        assert node._gate_state == 'suppressed_status'
        assert node._last_emit_ns is None

        # Rewind _last_msg_ns past stale_warn but not stale_error.
        elapsed = int((node._stale_warn + 0.5) * 1e9)
        node._last_msg_ns -= elapsed
        level, msg_text = _capture_diag(node)
        assert level == DiagnosticStatus.WARN, (level, msg_text)
        assert 'SbgUtcTime stale' in msg_text, msg_text
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_diagnostic_regate_escalates_to_error_past_stale_error(mock_serial_cls):
    """Prolonged re-gating (past stale_age_error) escalates to ERROR."""
    mock_serial_cls.return_value = MagicMock()
    node = ZdaSerialBridgeNode()
    try:
        # Open + emit, then re-gate via require_utc_sync drop.
        node._on_utc_time(_make_msg(clock_utc_status=2, clock_utc_sync=True))
        node._require_utc_sync = True
        node._on_utc_time(_make_msg(clock_utc_status=2, clock_utc_sync=False))
        assert node._gate_state == 'suppressed_sync'

        # Push _last_emit_ns past stale_error.
        elapsed = int((node._stale_error + 1.0) * 1e9)
        node._last_emit_ns -= elapsed
        level, msg_text = _capture_diag(node)
        assert level == DiagnosticStatus.ERROR, (level, msg_text)
        assert 're-gated for' in msg_text, msg_text
        assert 'clock_utc_sync=False' in msg_text, msg_text
    finally:
        node.destroy_node()


# --------------------------------------------------------------- reconnect


@patch('zda_serial_bridge.node.serial.Serial')
def test_reconnect_after_write_failure(mock_serial_cls):
    """Write failure closes the port; next valid message re-opens it."""
    failing_port = MagicMock()
    failing_port.write.side_effect = serial.SerialException('cable yanked')
    healthy_port = MagicMock()
    # Two successive serial.Serial(...) calls: the first returns the
    # failing port (used at startup); the reconnect attempt returns
    # the healthy port.
    mock_serial_cls.side_effect = [failing_port, healthy_port]

    node = ZdaSerialBridgeNode()
    try:
        # Bypass the reconnect-delay rate limit for the test.
        node._reconnect_delay = 0.0

        # First valid message: write fails → port closed, error counted.
        node._on_utc_time(_make_msg(clock_utc_status=2))
        assert node._serial is None, (
            'failed write should close the port'
        )
        assert node._serial_error_count == 1
        assert node._write_count == 0
        assert 'write error' in node._last_status_text

        # Reset the rate-limit clock so the next call attempts a re-open.
        node._last_open_attempt_ns = 0

        # Next valid message: reconnect succeeds, write goes through.
        node._on_utc_time(_make_msg(clock_utc_status=2))
        assert node._serial is healthy_port
        assert node._write_count == 1
        assert healthy_port.write.call_count == 1
        assert node._last_status_text == 'OK'
        # serial.Serial called exactly twice: initial open + one reconnect.
        assert mock_serial_cls.call_count == 2
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_reconnect_respects_rate_limit(mock_serial_cls):
    """A second open attempt within reconnect_delay must be skipped."""
    failing_port = MagicMock()
    failing_port.write.side_effect = serial.SerialException('cable yanked')
    healthy_port = MagicMock()
    mock_serial_cls.side_effect = [failing_port, healthy_port]

    node = ZdaSerialBridgeNode()
    try:
        # Keep the default ~2 s reconnect_delay; the rate-limit clock
        # was set during the initial _open_serial in __init__, so a
        # write-failure followed immediately by another _on_utc_time
        # should NOT trigger another open attempt yet.
        node._on_utc_time(_make_msg(clock_utc_status=2))
        assert node._serial is None
        opens_before = mock_serial_cls.call_count

        # Immediate second call: rate-limit must suppress the open.
        node._on_utc_time(_make_msg(clock_utc_status=2))
        assert mock_serial_cls.call_count == opens_before, (
            'open attempt within reconnect_delay should be rate-limited'
        )
        assert node._serial is None
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_diagnostic_timer_attempts_reconnect_when_serial_closed(mock_serial_cls):
    """Reconnect runs from the diagnostic timer, not only from _on_utc_time."""
    # First Serial(...) call fails; the diagnostic-timer reopen succeeds.
    healthy_port = MagicMock()
    mock_serial_cls.side_effect = [
        serial.SerialException('no device at boot'),
        healthy_port,
    ]
    node = ZdaSerialBridgeNode()
    try:
        # Startup open failed → _serial is None, no further message flow.
        assert node._serial is None
        node._reconnect_delay = 0.0
        node._last_open_attempt_ns = 0

        # No _on_utc_time call — the diagnostic tick alone must reopen.
        _capture_diag(node)

        assert node._serial is healthy_port, (
            'diagnostic timer should trigger _open_serial when port is closed'
        )
        assert mock_serial_cls.call_count == 2
    finally:
        node.destroy_node()


@patch('zda_serial_bridge.node.serial.Serial')
def test_open_failure_at_startup_keeps_node_alive(mock_serial_cls):
    """Startup with serial open failing must not crash; node stays up."""
    mock_serial_cls.side_effect = serial.SerialException('no device')

    node = ZdaSerialBridgeNode()
    try:
        assert node._serial is None
        assert node._serial_error_count == 1
        # Diagnostics should report ERROR for the closed port.
        level, msg_text = _capture_diag(node)
        assert level == DiagnosticStatus.ERROR
        assert 'Serial not connected' in msg_text
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
