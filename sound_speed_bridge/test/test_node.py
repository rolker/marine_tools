"""
Node-level tests for the two byte-level passthrough publishers.

The ``raw`` topic exists so deployment bags capture the bytes of each
framed serial sentence — including sentences that fail to parse, the key
diagnostic case — making post-hoc RCA of garbled traffic possible without
a live serial capture. It is a per-sentence passthrough, not a wire tap:
inter-sentence padding is stripped by the parser and a stream that never
frames (e.g. wrong baud) publishes nothing there.

The ``serial_tap`` topic covers exactly that gap (rolker/marine_tools#77):
it carries every chunk ``ser.read()`` returns, verbatim and pre-framing, so
unframeable garbage — the field failure mode, where bus-voltage sag
corrupts the line terminator — still reaches the bag. It is gated on the
``serial_tap_enabled`` parameter, which defaults to **off**: while off the
publisher does not exist and the topic is not advertised at all. The
parameter is toggled live through the node's set-parameters callback, so
the tap tests below enable it explicitly
(``_make_node(..., tap_enabled=True)``) and a dedicated group covers the
default-off, topic-lifecycle and runtime-toggle behaviour.

``raw`` tests exercise :meth:`SoundSpeedBridgeNode._handle_reading`
directly with mocked serial I/O, following the
``zda_serial_bridge/test/test_node.py`` pattern; ``serial_tap`` tests must
drive :meth:`SoundSpeedBridgeNode._serial_loop` itself, via the
``_drive_serial_loop`` harness below, because the tap publish lives there.
"""

import math
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

from diagnostic_msgs.msg import DiagnosticArray
import pytest
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.parameter import Parameter
from sound_speed_bridge.node import main, SoundSpeedBridgeNode
from sound_speed_bridge.parsers import RegexParser, SoundSpeedReading
from std_msgs.msg import UInt8MultiArray


@pytest.fixture(autouse=True)
def _ros_context():
    rclpy.init()
    yield
    rclpy.shutdown()


def _set_tap_enabled(node, value):
    """
    Set ``serial_tap_enabled`` the way an operator does, and assert it took.

    ``set_parameters`` runs the node's registered set-parameters callback
    synchronously, so no spin is needed and the flag is applied by the time
    this returns — the same path ``ros2 param set`` drives.
    """
    results = node.set_parameters(
        [Parameter('serial_tap_enabled', Parameter.Type.BOOL, value)])
    assert all(r.successful for r in results), [r.reason for r in results]
    assert node._serial_tap_enabled is value
    assert (node._tap_pub is not None) is value
    assert node.get_parameter('serial_tap_enabled').value is value


def _make_node(mock_serial_cls, tap_enabled: bool = False) -> SoundSpeedBridgeNode:
    """
    Build a real node with the serial port mocked out and its serial thread stopped.

    The node's background serial thread starts in ``__init__``. The mocked
    port's ``read`` returns ``b''`` so the loop never feeds a MagicMock into
    ``parser.feed()``, but ``b''`` also returns instantly — leaving the thread
    running would busy-spin at full CPU for the node's lifetime. These tests
    drive :meth:`SoundSpeedBridgeNode._handle_reading` directly and never need
    the thread, so it is stopped deterministically right after construction.

    ``serial_tap`` is off by default (operator decision, #77) and its
    publisher does not exist until enabled, so the tap tests pass
    ``tap_enabled=True``. It is enabled the way an operator does it — through
    ``set_parameters`` and therefore through the node's real set-parameters
    callback, which is what creates the publisher — rather than by poking node
    internals, so every tap test also exercises the enable path it depends on.
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
        # Off by default means no publisher at all, not a silent one.
        assert node._tap_pub is None
        assert node._serial_tap_enabled is False
        if tap_enabled:
            _set_tap_enabled(node, True)
            # Same external contract as `raw`, checked on the real publisher
            # the enable path just created and before any test swaps in a
            # mock: a bare relative `serial_tap` of type UInt8MultiArray, so a
            # rename is caught here rather than by a deployment bag that turns
            # out to be missing the topic.
            assert node._tap_pub.topic_name == '/serial_tap'
            assert node._tap_pub.msg_type is UInt8MultiArray
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


# --- Accumulation-buffer cap (rolker/marine_tools#78) -----------------------


def _reinit_with_overrides(*overrides: str) -> None:
    """
    Restart the ROS context with global parameter overrides.

    The node takes no constructor arguments, so a parameter override has to
    come from the context's ``--ros-args``. The autouse fixture's shutdown
    still applies to the context created here.
    """
    rclpy.shutdown()
    args = ['--ros-args']
    for override in overrides:
        args += ['-p', override]
    rclpy.init(args=args)


@patch('sound_speed_bridge.node.serial.Serial')
def test_parser_max_buffer_bytes_reaches_the_parser(mock_serial_cls):
    """A configured cap is validated and handed to the parser instance."""
    _reinit_with_overrides('parser_max_buffer_bytes:=512')
    node = _make_node(mock_serial_cls)
    try:
        assert node._parser_max_buffer_bytes == 512
        assert node._parser._max_buffer_bytes == 512
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_parser_max_buffer_bytes_below_floor_is_rejected(mock_serial_cls):
    """
    A cap below the serial read size fails at startup, naming the parameter.

    Validated in the node as well as in the parser so the operator sees the
    parameter they set, not a constructor argument they never wrote.
    """
    _reinit_with_overrides('parser_max_buffer_bytes:=128')
    port = MagicMock()
    port.read.return_value = b''
    mock_serial_cls.return_value.__enter__.return_value = port
    with pytest.raises(ValueError, match='parser_max_buffer_bytes'):
        SoundSpeedBridgeNode()


@patch('sound_speed_bridge.node.serial.Serial')
def test_parser_max_buffer_bytes_is_read_only(mock_serial_cls):
    """
    The cap is declared read-only, so a field `ros2 param set` is rejected.

    It is read once at construction and handed to the parser; a runtime set
    that reported success and changed nothing would be worse than a refusal.
    """
    node = _make_node(mock_serial_cls)
    try:
        assert node.describe_parameter('parser_max_buffer_bytes').read_only
        result = node.set_parameters(
            [Parameter('parser_max_buffer_bytes', Parameter.Type.INTEGER, 1024)])
        assert not result[0].successful
        assert node._parser._max_buffer_bytes != 1024
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_main_reports_a_wrong_typed_parameter_and_shuts_down(mock_serial_cls):
    """
    An override of the wrong ROS type is the same refused start.

    rclpy raises InvalidParameterTypeException at declaration, before our
    own validation runs; it must reach the same one FATAL line and exit 1.
    """
    port = MagicMock()
    port.read.return_value = b''
    mock_serial_cls.return_value.__enter__.return_value = port
    rclpy.shutdown()
    logger = MagicMock()
    try:
        with patch('sound_speed_bridge.node.rclpy.logging.get_logger',
                   return_value=logger):
            with pytest.raises(SystemExit) as excinfo:
                main(args=['--ros-args',
                           '-p', 'parser_max_buffer_bytes:=4096.0'])
        assert excinfo.value.code == 1
        assert not rclpy.ok()
        assert 'parser_max_buffer_bytes' in logger.fatal.call_args.args[0]
    finally:
        rclpy.init()


@patch('sound_speed_bridge.node.serial.Serial')
def test_main_reports_a_rejected_parameter_and_shuts_down(mock_serial_cls):
    """
    A refused parameter exits 1 through one FATAL line, not a traceback.

    The node constructing outside main()'s try/finally meant a parameter
    ValueError skipped rclpy.shutdown() entirely and printed a stack trace
    an operator has to read backwards to find the parameter name in.
    """
    port = MagicMock()
    port.read.return_value = b''
    mock_serial_cls.return_value.__enter__.return_value = port
    rclpy.shutdown()  # main() does its own init
    logger = MagicMock()
    try:
        with patch('sound_speed_bridge.node.rclpy.logging.get_logger',
                   return_value=logger):
            with pytest.raises(SystemExit) as excinfo:
                main(args=['--ros-args',
                           '-p', 'parser_max_buffer_bytes:=128'])
        # A refused start must look like a failure to ros2 launch and to
        # systemd Restart=on-failure, not like a clean shutdown.
        assert excinfo.value.code == 1
        assert not rclpy.ok()
        assert 'parser_max_buffer_bytes' in logger.fatal.call_args.args[0]
    finally:
        rclpy.init()  # restore the context the autouse fixture shuts down


@patch('sound_speed_bridge.node.serial.Serial')
def test_buffer_counters_surface_in_diagnostics(mock_serial_cls):
    """Both trim counters are published as /diagnostics KeyValues."""
    node = _make_node(mock_serial_cls)
    try:
        node._diag_pub = MagicMock()
        node._parser._trim_stats = (4321, 7)
        node._publish_diagnostics()
        status = node._diag_pub.publish.call_args.args[0].status[0]
        values = {kv.key: kv.value for kv in status.values}
        assert values['buffer_dropped_bytes'] == '4321'
        assert values['buffer_trim_count'] == '7'
    finally:
        node.destroy_node()


class _CountingCounters:
    """Parser stand-in that records how often the trim snapshot is read."""

    def __init__(self, dropped, trims):
        self._dropped = dropped
        self._trims = trims
        self.reads = 0

    @property
    def trim_stats(self):
        self.reads += 1
        # Simulate the serial thread bumping the pair between reads: a
        # second read in the same tick would see different values.
        self._dropped += 1000
        self._trims += 1
        return (self._dropped, self._trims)

    @property
    def buffer_dropped_bytes(self):
        raise AssertionError(
            'the node must take the trim_stats snapshot, not the '
            'individual counters: separate reads can straddle a trim')

    @property
    def buffer_trim_count(self):
        raise AssertionError(
            'the node must take the trim_stats snapshot, not the '
            'individual counters: separate reads can straddle a trim')


@patch('sound_speed_bridge.node.serial.Serial')
def test_trim_counters_are_snapshotted_once_per_tick(mock_serial_cls):
    """
    The WARN text and the published KeyValues come from one snapshot.

    The two counters are a correlated pair bumped on the serial thread. Read
    separately -- once for the WARN, again for the KeyValues, or once per
    counter -- they can describe instants a chunk apart, so an operator
    correlating the log with /diagnostics sees numbers that do not add up.
    The pair is taken as one ``trim_stats`` tuple, exactly once per tick;
    the stand-in fails loudly if either individual counter is touched.
    """
    node = _make_node(mock_serial_cls)
    logger = MagicMock()
    try:
        node._diag_pub = MagicMock()
        node.get_logger = MagicMock(return_value=logger)
        node._parser = _CountingCounters(dropped=0, trims=0)

        node._publish_diagnostics()

        assert node._parser.reads == 1  # one snapshot of the pair
        status = node._diag_pub.publish.call_args.args[0].status[0]
        values = {kv.key: kv.value for kv in status.values}
        assert values['buffer_dropped_bytes'] == '1000'
        assert values['buffer_trim_count'] == '1'
        warn = logger.warning.call_args.args[0]
        assert '1000 B since' in warn
        assert '1000 B over 1 trims' in warn
    finally:
        del node.get_logger
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_buffer_trim_warns_once_then_backs_off(mock_serial_cls):
    """
    The first trim warns immediately; the next ones are held back.

    A framing stall trims once per serial read for as long as it lasts
    (1-5 h in the field), so warning on every diagnostics tick would be
    ~18k lines on top of the stale-reading ERROR this method already emits.
    """
    node = _make_node(mock_serial_cls)
    logger = MagicMock()
    try:
        node._diag_pub = MagicMock()
        node.get_logger = MagicMock(return_value=logger)

        node._parser._trim_stats = (100, 1)
        node._publish_diagnostics()
        assert logger.warning.call_count == 1
        assert '100 B since' in logger.warning.call_args.args[0]

        # A further trim on the very next tick is inside the back-off.
        node._parser._trim_stats = (200, 2)
        node._publish_diagnostics()
        assert logger.warning.call_count == 1

        # No new trim at all: also silent.
        node._publish_diagnostics()
        assert logger.warning.call_count == 1

        # Once the interval has elapsed a new trim warns again, reporting
        # only what was dropped since the previous warning.
        node._last_trim_warn_ns -= 10 * 1_000_000_000
        node._parser._trim_stats = (350, 3)
        node._publish_diagnostics()
        assert logger.warning.call_count == 2
        assert '250 B since' in logger.warning.call_args.args[0]
    finally:
        del node.get_logger
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_buffer_trim_warn_backoff_resets_after_a_quiet_period(mock_serial_cls):
    """A later, separate stall is loud again once the ceiling has passed."""
    node = _make_node(mock_serial_cls)
    logger = MagicMock()
    try:
        node._diag_pub = MagicMock()
        node.get_logger = MagicMock(return_value=logger)

        node._parser._trim_stats = (100, 1)
        node._publish_diagnostics()
        assert logger.warning.call_count == 1
        node._parser._trim_stats = (100, 2)
        node._publish_diagnostics()
        assert logger.warning.call_count == 1

        # A long gap since the last WARN is not a quiet period: the stall
        # is still trimming, it is just inside the back-off. The reset is
        # anchored to the last trim observed, not the last WARN.
        quiet_ns = int((node._TRIM_WARN_MAX_INTERVAL_S + 1.0) * 1_000_000_000)
        node._last_trim_warn_ns -= quiet_ns
        node._publish_diagnostics()
        assert node._trim_warn_interval_s != 0.0

        # Quiet for longer than the back-off ceiling, with no new trims.
        node._last_trim_seen_ns -= quiet_ns
        node._publish_diagnostics()
        assert logger.warning.call_count == 1
        assert node._trim_warn_interval_s == 0.0

        # The next stall warns on its first trim.
        node._parser._trim_stats = (500, 3)
        node._publish_diagnostics()
        assert logger.warning.call_count == 2
    finally:
        del node.get_logger
        node.destroy_node()


# --- Serial-thread survival (non-finite sentences) --------------------------


def _serial_port_replaying(chunks):
    """Build a mocked serial port that replays chunks, then idles on empty reads."""
    remaining = list(chunks)

    def _read(*args, **kwargs):
        if remaining:
            return remaining.pop(0)
        time.sleep(0.01)  # idle without burning a core on b'' reads
        return b''

    port = MagicMock()
    port.read.side_effect = _read
    return port


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_thread_survives_a_non_finite_sentence(mock_serial_cls):
    """
    A nan/inf sentence must not kill the reader thread.

    This is the whole point of the parser's finiteness guard: the loop
    catches only (SerialException, OSError), so a conversion raising
    ValueError/OverflowError out of feed() ends the daemon thread with
    _serial_connected still True -- the node reports a live serial link
    and never publishes another reading, for the rest of the deployment.

    ``1e306`` is the finite-in-m/s, infinite-in-mm/s case: it survives a
    finiteness test on the value alone, and its exact Decimal ``raw_mm_s``
    then rides downstream into ``round()``/``{value_mm_s}`` on this same
    thread.
    """
    port = _serial_port_replaying(
        [b'nan\r\r\n', b'inf\r\r\n', b'1e999\r\r\n', b'1e306\r\r\n',
         b'1500.500\r\r\n'])
    mock_serial_cls.return_value.__enter__.return_value = port
    node = SoundSpeedBridgeNode()
    try:
        last = None
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            with node._lock:
                last = node._last_reading
            if last is not None and not math.isnan(last.sound_speed_m_s):
                break
            time.sleep(0.01)
        assert node._serial_thread.is_alive()
        assert node._serial_connected
        assert last is not None, 'no reading published; the thread died'
        assert last.sound_speed_m_s == 1500.5
        assert last.raw_mm_s == 1500500
        assert node._parse_error_count == 4
    finally:
        node.destroy_node()


def _drive_serial_loop(
    node, mock_serial_cls, chunks, stop_before_index=None, before_chunk=None,
):
    """
    Run :meth:`SoundSpeedBridgeNode._serial_loop` synchronously over ``chunks``.

    ``_make_node`` deliberately stops the node's serial thread (its mocked
    ``read`` returns ``b''`` instantly, so a live thread busy-spins at full
    CPU), and the ``raw`` tests call ``_handle_reading`` directly. The tap
    publish, however, lives inside the loop, so these tests need the loop
    itself — run here on the test thread, with no live thread and no sleeps:

    - ``read`` pops the next chunk; once the chunks are exhausted it sets the
      stop event and returns ``b''``, which the loop's ``if not data:
      continue`` turns into a re-test of the ``while`` condition, so the loop
      exits deterministically.
    - ``stop_before_index`` sets the stop event *before* returning the chunk
      at that index, which is how the shutdown-guard test reaches the tap
      publish with the event already set.
    - ``before_chunk(index)``, when given, is called just before the chunk at
      that index is returned from ``read``. That is the only point at which a
      test can change node state *between* two chunks of one synchronous loop
      run, which is what the runtime-toggle test needs: it flips
      ``serial_tap_enabled`` through the real set-parameters callback while
      the reader is mid-stream.

    The real parser is left in place so "garbage yields no readings" is
    asserted against the actual framing code.
    """
    port = MagicMock()
    remaining = list(chunks)
    state = {'index': 0}

    def _read(_size):
        if not remaining:
            node._stop_event.set()
            return b''
        index = state['index']
        state['index'] += 1
        if stop_before_index is not None and index == stop_before_index:
            node._stop_event.set()
        if before_chunk is not None:
            before_chunk(index)
        return remaining.pop(0)

    port.read.side_effect = _read
    mock_serial_cls.return_value.__enter__.return_value = port
    node._stop_event.clear()
    node._serial_loop()
    assert not remaining, 'serial loop exited before consuming every chunk'
    return port


def _field_regex_parser():
    r"""
    Build the parser the BizzyBoat deployment launch configures, as it does.

    ``sound_speed_launch.py`` in unh_echoboats_project11 runs ``parser: regex``
    with a CRLF terminator — not the package's ``aml`` default, which frames on
    a single CR. The distinction matters here: with CRLF framing a corrupted
    ``\n`` (the observed ``\r\x00``) loses the terminator outright and the
    stream never frames at all, which is the silent-``raw`` condition #77
    reports. Assigned onto the constructed node rather than re-initialising the
    rclpy context with parameter overrides — the loop reads ``self._parser``.
    """
    return RegexParser(
        pattern=r'\$AML,SVM,(?P<sound_speed>\d+\.\d+)',
        line_terminator='crlf',
    )


# Field bytes from #77: two AML SVM sentences whose CRLF terminators have been
# corrupted to CR NUL by bus-voltage sag (and one with a digit knocked out of
# the serial number). Under the field CRLF framing these never terminate a
# line, so the parser yields nothing and `raw` stays silent.
_FIELD_GARBAGE = (
    b'$AML,SVM,1515.217,SN,200937*05\r\x00'
    b'$AML,SVM,1515.180,SN,20 937*08\r\x00'
)


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_publishes_read_chunk(mock_serial_cls):
    """One ser.read() chunk publishes verbatim as one serial_tap message."""
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        node._tap_pub = MagicMock()
        chunk = b'1500.123\r\r\n'
        _drive_serial_loop(node, mock_serial_cls, [chunk])
        assert node._tap_pub.publish.call_count == 1
        msg = node._tap_pub.publish.call_args.args[0]
        assert bytes(msg.data) == chunk
    finally:
        node.destroy_node()


# --- Shutdown: a deliberate stop is exit 0 ----------------------------------

_SIGINT_HARNESS = """
import os
import signal
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import sound_speed_bridge.node as node_mod

port = MagicMock()


def _read(*args, **kwargs):
    time.sleep(0.02)
    return b''


port.read.side_effect = _read


def _interrupt():
    time.sleep(2.0)
    os.kill(os.getpid(), signal.SIGINT)


threading.Thread(target=_interrupt, daemon=True).start()
with patch.object(node_mod.serial, 'Serial') as serial_cls:
    serial_cls.return_value.__enter__.return_value = port
    sys.exit(node_mod.main())
"""


def test_sigint_exits_zero_without_a_traceback(tmp_path):
    """
    A real SIGINT to the console entry point exits 0 and prints no traceback.

    rclpy's own signal handler shuts the context down before main()'s
    finally runs, so rclpy.shutdown() raised RCLError ("rcl_shutdown
    already called") and spin() raised an uncaught
    ExternalShutdownException: Ctrl-C exited **1** with two tracebacks.
    Under systemd Restart=on-failure or a launch file's on-exit handler,
    an operator stopping the node deliberately was indistinguishable from
    a crash -- and this package now exits 1 for a genuinely refused
    parameter, which that noise would hide.

    This runs the real entry point in a subprocess with the serial port
    mocked out, because the behaviour under test *is* signal delivery.
    """
    script = tmp_path / 'sigint_main.py'
    script.write_text(_SIGINT_HARNESS)
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=120)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, f'exit {proc.returncode}\n{combined}'
    assert 'Traceback' not in combined, combined
    assert 'rcl_shutdown already called' not in combined, combined


@patch('sound_speed_bridge.node.serial.Serial')
def test_main_returns_cleanly_on_an_external_shutdown(mock_serial_cls):
    """
    An externally shut-down context ends main() quietly, not with a raise.

    The in-process twin of the SIGINT test: it pins both halves of the fix
    (catching ExternalShutdownException, and try_shutdown() over shutdown())
    deterministically, without depending on signal delivery.
    """
    port = _serial_port_replaying([])
    mock_serial_cls.return_value.__enter__.return_value = port

    def _spin(_node):
        rclpy.utilities.get_default_context().shutdown()
        raise ExternalShutdownException()

    rclpy.shutdown()  # main() does its own init
    try:
        with patch('sound_speed_bridge.node.rclpy.spin', side_effect=_spin):
            main()  # must not raise
        assert not rclpy.ok()
    finally:
        rclpy.init()  # restore the context the autouse fixture shuts down


@patch('sound_speed_bridge.node.serial.Serial')
def test_a_valueerror_from_spin_is_not_reported_as_a_start_failure(mock_serial_cls):
    """
    The construction `except ValueError` must not span spin().

    A ValueError raised later, from a callback during spin, is not a start
    failure: logging it as one ("sound_speed_bridge failed to start") sends
    an operator hunting a parameter problem that does not exist, and would
    convert a mid-run fault into the refusal exit code. Widening the except
    back over spin() must fail this test.
    """
    port = _serial_port_replaying([])
    mock_serial_cls.return_value.__enter__.return_value = port
    logger = MagicMock()
    rclpy.shutdown()  # main() does its own init
    try:
        with patch('sound_speed_bridge.node.rclpy.logging.get_logger',
                   return_value=logger):
            with patch('sound_speed_bridge.node.rclpy.spin',
                       side_effect=ValueError('mid-run fault')):
                with pytest.raises(ValueError, match='mid-run fault'):
                    main()
        assert logger.fatal.call_count == 0
        assert not rclpy.ok()
    finally:
        rclpy.init()  # restore the context the autouse fixture shuts down


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_captures_unframeable_field_garbage(mock_serial_cls):
    """
    Corrupted field bytes that never frame still reach the bag.

    This is the whole point of #77: with the terminator corrupted, the parser
    yields no readings and `raw` publishes nothing, so before this topic
    existed the bag showed silence — indistinguishable from a dead probe.
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        node._parser = _field_regex_parser()
        node._tap_pub = MagicMock()
        node._raw_pub = MagicMock()
        node._pub = MagicMock()
        _drive_serial_loop(node, mock_serial_cls, [_FIELD_GARBAGE])
        assert node._raw_pub.publish.call_count == 0
        assert node._pub.publish.call_count == 0
        assert node._tap_pub.publish.call_count == 1
        msg = node._tap_pub.publish.call_args.args[0]
        assert bytes(msg.data) == _FIELD_GARBAGE
        assert node._tap_byte_count == len(_FIELD_GARBAGE)
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_captures_all_nul_chunk(mock_serial_cls):
    """
    An all-NUL chunk — a wrong-baud signature — publishes verbatim.

    Nothing frames, so `raw` is silent; the tap must still show that bytes
    were arriving, which is what separates "wrong baud" from "no probe".
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        node._parser = _field_regex_parser()
        node._tap_pub = MagicMock()
        node._raw_pub = MagicMock()
        chunk = b'\x00' * 64
        _drive_serial_loop(node, mock_serial_cls, [chunk])
        assert node._raw_pub.publish.call_count == 0
        assert node._tap_pub.publish.call_count == 1
        assert bytes(node._tap_pub.publish.call_args.args[0].data) == chunk
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_concatenation_reconstructs_stream(mock_serial_cls):
    """
    Concatenating the tap messages reproduces the wire stream byte-exactly.

    Chunks are split at awkward places — mid-value and mid-terminator — so a
    boundary bug (dropped, duplicated, or reordered bytes) shows up. The
    parser still frames across the same boundaries, so the tap is shown not
    to disturb the primary path.
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        node._tap_pub = MagicMock()
        node._pub = MagicMock()
        chunks = [b'1500.1', b'23\r\r\n1499', b'.900\r', b'\r\n', b'\x00\xff']
        _drive_serial_loop(node, mock_serial_cls, chunks)
        published = [
            bytes(call.args[0].data) for call in node._tap_pub.publish.call_args_list
        ]
        assert published == chunks
        assert b''.join(published) == b''.join(chunks)
        assert node._tap_byte_count == sum(len(c) for c in chunks)
        # The two complete sentences still parsed and published normally.
        assert node._pub.publish.call_count == 2
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_noop_after_stop(mock_serial_cls):
    """
    A chunk read after the stop event is set publishes nothing.

    destroy_node()'s 2 s join is best-effort and ser.read() can block up to
    1 s past it, so the loop may return one last chunk after the publishers
    are destroyed — the tap's shutdown guard must swallow it.
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        node._tap_pub = MagicMock()
        _drive_serial_loop(
            node, mock_serial_cls, [b'1500.123\r\r\n'], stop_before_index=0)
        assert node._tap_pub.publish.call_count == 0
        assert node._tap_byte_count == 0
    finally:
        node._stop_event.clear()
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_publish_failure_is_counted_not_fatal(mock_serial_cls):
    """
    A raising tap publish must not kill the serial reader thread.

    _serial_loop catches only (SerialException, OSError), so anything else
    escaping the diagnostic publish would end the reader permanently — in
    exactly the degraded condition the tap exists to observe. The failure is
    counted and logged instead, never silent.
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        node._tap_pub = MagicMock()
        node._tap_pub.publish.side_effect = RuntimeError('publisher destroyed')
        node._pub = MagicMock()
        chunks = [b'1500.123\r\r\n', b'1499.900\r\r\n']
        # Returns normally rather than propagating: both chunks are consumed.
        _drive_serial_loop(node, mock_serial_cls, chunks)
        assert node._tap_pub.publish.call_count == 2
        assert node._tap_error_count == 2
        # tap_byte_count is wire traffic, not publish success: the bytes did
        # arrive, so a failing tap must not look like a silent probe. The
        # failures are tap_error_count's to report.
        assert node._tap_byte_count == sum(len(c) for c in chunks)
        # The primary path kept running through both chunks.
        assert node._pub.publish.call_count == 2
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_tap_counters_surface_in_diagnostics(mock_serial_cls):
    """
    tap_byte_count / tap_error_count reach /diagnostics.

    Absence of serial_tap messages only means "the probe was silent" if the
    counter is in the bag to prove the node was running and reading — hence
    /diagnostics belongs in the deployment record list alongside the topic
    (rolker/unh_echoboats_project11#396).
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        node._tap_pub = MagicMock()
        chunks = [b'1500.123\r\r\n', b'\x00\x00\x00']
        _drive_serial_loop(node, mock_serial_cls, chunks)
        node._diag_pub = MagicMock()
        node._publish_diagnostics()
        assert node._diag_pub.publish.call_count == 1
        diag = node._diag_pub.publish.call_args.args[0]
        assert isinstance(diag, DiagnosticArray)
        values = {kv.key: kv.value for kv in diag.status[0].values}
        assert values['tap_byte_count'] == str(sum(len(c) for c in chunks))
        assert values['tap_error_count'] == '0'
    finally:
        node.destroy_node()


class _OrderRecordingParser:
    """
    Delegating parser that records when each chunk reaches the framing code.

    ``feed`` is a generator in every real parser, so its body runs when
    ``_serial_loop`` starts *consuming* it — which is exactly the moment the
    ordering invariant is about. Recording the marker inside the generator
    therefore timestamps the feed the way the loop experiences it, not the
    call that merely built it.
    """

    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def feed(self, data, receive_time_ns):
        self._log.append(('feed', bytes(data)))
        for reading in self._inner.feed(data, receive_time_ns):
            yield reading


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_publishes_after_parser_feed(mock_serial_cls):
    """
    The tap publish happens after the chunk's parser feed and primary publishes.

    The tap is diagnostic-only, so it must never delay or preempt the
    SoundSpeed path — the same rule ``_handle_reading`` states for ``raw``.
    That ordering was documented as load-bearing but unguarded: moving
    ``_publish_serial_tap(data)`` above the feed loop left every other test
    green. This test asserts the relative order directly, per chunk, so the
    mutation fails here.
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        log = []
        node._parser = _OrderRecordingParser(node._parser, log)
        node._pub = MagicMock()
        node._pub.publish.side_effect = (
            lambda msg: log.append(('sound_speed', round(msg.sound_speed, 3))))
        node._tap_pub = MagicMock()
        node._tap_pub.publish.side_effect = (
            lambda msg: log.append(('tap', bytes(msg.data))))
        chunks = [b'1500.123\r\r\n', b'1499.900\r\r\n']
        _drive_serial_loop(node, mock_serial_cls, chunks)
        assert log == [
            ('feed', chunks[0]),
            ('sound_speed', 1500.123),
            ('tap', chunks[0]),
            ('feed', chunks[1]),
            ('sound_speed', 1499.900),
            ('tap', chunks[1]),
        ]
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_disabled_by_default_publishes_nothing(mock_serial_cls):
    """
    With no parameter set, the tap publishes nothing but still counts bytes.

    The operator decision for #77 is that the tap is off unless someone is
    asking a framing question, so "default" here is the field's normal state:
    no serial_tap messages in the bag, no bag volume spent, and the primary
    SoundSpeed path completely unaffected. tap_byte_count keeps counting,
    because it is the always-on, zero-cost answer to "is the probe silent?" —
    an operator reading a frozen counter knows there is nothing to enable the
    tap for, without having enabled it first.

    The topic's own absence is covered by
    ``test_serial_tap_topic_advertised_only_when_enabled``.
    """
    node = _make_node(mock_serial_cls)
    try:
        node._raw_pub = MagicMock()
        node._pub = MagicMock()
        chunks = [b'1500.123\r\r\n', b'\x00' * 16]
        _drive_serial_loop(node, mock_serial_cls, chunks)
        # No mock is installed on the tap: the publisher's absence *is* the
        # disabled state, so assigning one would enable the tap and test
        # nothing. Nothing can have been published, and nothing errored.
        assert node._tap_pub is None
        assert node._tap_error_count == 0
        # Wire bytes are still counted while disabled.
        assert node._tap_byte_count == sum(len(c) for c in chunks)
        # The primary path is untouched by the gate: the parseable sentence
        # still produced its SoundSpeed and its raw passthrough.
        assert node._pub.publish.call_count == 1
        assert node._raw_pub.publish.call_count == 1
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_enabled_by_parameter_publishes(mock_serial_cls):
    """
    Enabling the parameter on a node that started disabled turns publishing on.

    Enabling on the fly is the whole point of the parameter, so this drives
    the real operator path — set_parameters, hence the node's registered
    set-parameters callback — on a node constructed with the default.
    """
    node = _make_node(mock_serial_cls)
    try:
        assert node._serial_tap_enabled is False
        _set_tap_enabled(node, True)
        node._tap_pub = MagicMock()
        chunk = b'1500.123\r\r\n'
        _drive_serial_loop(node, mock_serial_cls, [chunk])
        assert node._tap_pub.publish.call_count == 1
        assert bytes(node._tap_pub.publish.call_args.args[0].data) == chunk
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_runtime_toggle_takes_effect_mid_stream(mock_serial_cls):
    """
    An off -> on -> off toggle applies to the very next chunk, with no restart.

    The gate is read per chunk rather than latched at startup, so a set that
    lands while the reader is mid-stream must change what the *next* chunk
    does — which is what an operator enabling the tap during a deployment is
    relying on. The toggles here run through the real set-parameters callback
    between chunks of one synchronous loop run.
    """
    node = _make_node(mock_serial_cls)
    try:
        node._pub = MagicMock()
        chunks = [b'1500.100\r\r\n', b'1500.200\r\r\n', b'1500.300\r\r\n']
        published = []

        def _toggle(index):
            if index == 1:
                _set_tap_enabled(node, True)
                # Recorder installed on the publisher the enable just created
                # — there is none to install it on beforehand.
                node._tap_pub.publish = (
                    lambda msg: published.append(bytes(msg.data)))
            elif index == 2:
                _set_tap_enabled(node, False)

        _drive_serial_loop(node, mock_serial_cls, chunks, before_chunk=_toggle)
        assert node._tap_pub is None
        # Exactly the chunk read while enabled — not the one before, not the
        # one after: the toggle is neither late by a chunk nor sticky.
        assert published == [chunks[1]]
        # Every byte counted, enabled or not.
        assert node._tap_byte_count == sum(len(c) for c in chunks)
        # All three sentences still reached the primary path.
        assert node._pub.publish.call_count == 3
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_enabled_rejects_non_bool(mock_serial_cls):
    """
    A non-bool serial_tap_enabled is rejected, and the tap stays as it was.

    Both layers are checked: rclpy's own type check against the declared
    BOOL type (which rejects the set before the callback is reached — it
    returns an unsuccessful result, it does not raise), and the callback's
    own validation, called directly the way rclpy would call it. The second
    layer is not redundant: it is the contract a caller reaching the callback
    by any other route relies on.
    The callback must not coerce — bool('false') is True, so coercion would
    enable the tap for an operator who asked for the opposite.
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        results = node.set_parameters(
            [Parameter('serial_tap_enabled', Parameter.Type.STRING, 'false')])
        assert [r.successful for r in results] == [False]
        assert 'BOOL' in results[0].reason
        assert node._serial_tap_enabled is True

        result = node._on_set_parameters(
            [Parameter('serial_tap_enabled', Parameter.Type.STRING, 'false')])
        assert result.successful is False
        assert 'bool' in result.reason
        assert node._serial_tap_enabled is True
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_set_parameters_callback_leaves_other_parameters_alone(mock_serial_cls):
    """
    The callback accepts unrelated parameters and does not touch the tap flag.

    Recorded as a deliberate decision (#77): every other parameter of this
    node is read once at startup, and registering this callback does not
    change that — it neither applies nor rejects them, which is exactly what
    rclpy did before the callback existed. Rejecting startup parameters (as
    garmin_sidescan's node does) is a separate, wider change.
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        results = node.set_parameters(
            [Parameter('variance', Parameter.Type.DOUBLE, 0.25)])
        assert all(r.successful for r in results)
        assert node._serial_tap_enabled is True
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_enabled_surfaces_in_diagnostics(mock_serial_cls):
    """
    /diagnostics reports whether the tap is currently publishing.

    Now that the tap defaults to off, an absence of serial_tap messages in a
    bag is ambiguous between "the probe was silent" and "nobody switched the
    tap on". tap_byte_count separates silent from corrupt; this key separates
    both from not-enabled, so an RCA can tell them apart from the bag alone.
    """
    node = _make_node(mock_serial_cls)
    try:
        node._diag_pub = MagicMock()
        node._publish_diagnostics()
        values = {
            kv.key: kv.value
            for kv in node._diag_pub.publish.call_args.args[0].status[0].values
        }
        assert values['serial_tap_enabled'] == 'false'

        _set_tap_enabled(node, True)
        node._publish_diagnostics()
        values = {
            kv.key: kv.value
            for kv in node._diag_pub.publish.call_args.args[0].status[0].values
        }
        assert values['serial_tap_enabled'] == 'true'
    finally:
        node.destroy_node()


def _advertised_topics(node, topic, present, timeout_sec=5.0):
    """
    Wait for ``topic`` to appear/disappear from the node's advertised topics.

    This is the ``ros2 topic list`` view — the node's own publishers as the
    ROS graph reports them — rather than an internal attribute, because the
    operator decision behind the gate is about what the graph shows. Graph
    updates are asynchronous, so this polls with a bounded deadline instead
    of asserting once; the ``/raw`` cross-check in the caller keeps an empty
    or broken graph query from passing a ``present=False`` assertion
    vacuously.
    """
    deadline = time.monotonic() + timeout_sec
    while True:
        names = [
            name for name, _ in node.get_publisher_names_and_types_by_node(
                node.get_name(), node.get_namespace())
        ]
        if (topic in names) == present:
            return names
        if time.monotonic() > deadline:
            raise AssertionError(
                f'{topic} {"missing from" if present else "still in"} '
                f'{names} after {timeout_sec}s')
        rclpy.spin_once(node, timeout_sec=0.05)


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_topic_advertised_only_when_enabled(mock_serial_cls):
    """
    The serial_tap topic exists exactly while the tap is enabled.

    The operator decision for #77 is "I would rather it only publish if we
    enable it": while disabled the topic must not be advertised at all, so
    `ros2 topic list` on an ordinary deployment shows no serial_tap and an
    operator sees the tap appear when, and only when, they switch it on.
    Asserted against the ROS graph, with /raw as the control — it is always
    advertised, so its presence proves the query is answering.
    """
    node = _make_node(mock_serial_cls)
    try:
        names = _advertised_topics(node, '/serial_tap', present=False)
        assert '/raw' in names

        _set_tap_enabled(node, True)
        names = _advertised_topics(node, '/serial_tap', present=True)
        assert '/raw' in names

        _set_tap_enabled(node, False)
        names = _advertised_topics(node, '/serial_tap', present=False)
        assert '/raw' in names
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_enabling_twice_keeps_the_same_publisher(mock_serial_cls):
    """
    A repeated enable (or disable) does not churn the publisher.

    An operator re-issuing `ros2 param set ... true` to confirm the state, or
    a launch file that sets what is already set, must not tear down and
    re-advertise a live topic underneath a subscriber that is recording it.
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        first = node._tap_pub
        _set_tap_enabled(node, True)
        assert node._tap_pub is first

        _set_tap_enabled(node, False)
        assert node._tap_pub is None
        # A second disable is a no-op rather than an error.
        _set_tap_enabled(node, False)
        assert node._tap_pub is None
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_enabled_at_launch_needs_no_runtime_set(mock_serial_cls):
    """
    `serial_tap_enabled:=true` at launch advertises and publishes immediately.

    Every other tap test constructs the node with the default and switches
    the tap on afterwards through set_parameters, so without this one the
    launch-time path — __init__ reading the parameter and calling
    _set_tap_publishing — is asserted by prose only: deleting that call
    leaves the rest of the suite green while shipping a boat whose
    `serial_tap_enabled:=true` in a launch file silently advertises nothing.

    SoundSpeedBridgeNode.__init__ forwards no parameter_overrides, so the
    override is supplied the way a launch file supplies it — as a global ROS
    argument on the context the node is constructed in, which means
    re-initialising the autouse fixture's context here.
    """
    rclpy.shutdown()
    rclpy.init(args=['--ros-args', '-p', 'serial_tap_enabled:=true'])
    port = MagicMock()
    port.read.return_value = b''
    mock_serial_cls.return_value.__enter__.return_value = port
    node = SoundSpeedBridgeNode()
    try:
        node._stop_event.set()
        node._serial_thread.join(timeout=2.0)
        assert not node._serial_thread.is_alive()
        node._stop_event.clear()
        # Enabled by construction alone: no set_parameters call has run.
        assert node.get_parameter('serial_tap_enabled').value is True
        assert node._serial_tap_enabled is True
        assert node._tap_pub is not None
        # Same external contract the runtime enable path is held to.
        assert node._tap_pub.topic_name == '/serial_tap'
        assert node._tap_pub.msg_type is UInt8MultiArray
        # ...and it publishes the very first chunk off the wire, so an
        # operator who launched with the tap on loses no bytes waiting for a
        # parameter set that never comes.
        node._tap_pub = MagicMock()
        chunk = b'1500.123\r\r\n'
        _drive_serial_loop(node, mock_serial_cls, [chunk])
        assert node._tap_pub.publish.call_count == 1
        assert bytes(node._tap_pub.publish.call_args.args[0].data) == chunk
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_tap_enable_publisher_failure_is_rejected_not_fatal(mock_serial_cls):
    """
    A failing create_publisher rejects the set instead of killing the bridge.

    rclpy wraps on-set callbacks in no try of its own and its executor
    re-raises a handler exception straight out of rclpy.spin(), so an
    RMW/resource failure during a live `ros2 param set serial_tap_enabled
    true` would otherwise take down the primary SoundSpeed path over a
    diagnostic topic. The set must degrade to an unsuccessful result with a
    reason, the tap must stay off, the parameter must keep its old value, and
    the node must keep reading and publishing.
    """
    node = _make_node(mock_serial_cls)
    try:
        with patch.object(node, 'create_publisher',
                          side_effect=RuntimeError('rmw out of resources')):
            results = node.set_parameters(
                [Parameter('serial_tap_enabled', Parameter.Type.BOOL, True)])
        assert not results[0].successful
        assert 'rmw out of resources' in results[0].reason
        # State is consistent: no publisher, tap reads as off, and the
        # rejected set left the parameter store untouched.
        assert node._tap_pub is None
        assert node._serial_tap_enabled is False
        assert node.get_parameter('serial_tap_enabled').value is False
        # The node is alive and the primary path is unaffected.
        node._raw_pub = MagicMock()
        node._pub = MagicMock()
        _drive_serial_loop(node, mock_serial_cls, [b'1500.123\r\r\n'])
        assert node._pub.publish.call_count == 1
        assert node._raw_pub.publish.call_count == 1
        # A later, non-failing enable still works — nothing was wedged.
        _set_tap_enabled(node, True)
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_tap_disable_publisher_failure_is_rejected_not_fatal(mock_serial_cls):
    """
    A failing destroy_publisher rejects the set instead of killing the bridge.

    The teardown half of the same guard. The documented consequence is that
    the reference is dropped rather than restored — rclpy has already
    removed the publisher from the node's registry by the time destroy can
    raise — so the tap reads as off (which is what it is) while the rejected
    set leaves the parameter store still saying true. The diagnostic key is
    derived from the publisher, so it reports the reality.
    """
    node = _make_node(mock_serial_cls, tap_enabled=True)
    try:
        with patch.object(node, 'destroy_publisher',
                          side_effect=RuntimeError('rmw teardown failed')):
            results = node.set_parameters(
                [Parameter('serial_tap_enabled', Parameter.Type.BOOL, False)])
        assert not results[0].successful
        assert 'rmw teardown failed' in results[0].reason
        assert node._tap_pub is None
        assert node._serial_tap_enabled is False
        # The rejected set leaves the parameter store saying True while the
        # publisher-derived state says off: the documented, deliberate
        # divergence (node.py, the destroy-failure comment). Pin it so a
        # change to either side fails here rather than silently.
        assert node.get_parameter('serial_tap_enabled').value is True
        # The node survived: the reader still runs and the primary path still
        # publishes.
        node._raw_pub = MagicMock()
        node._pub = MagicMock()
        _drive_serial_loop(node, mock_serial_cls, [b'1500.123\r\r\n'])
        assert node._pub.publish.call_count == 1
    finally:
        node.destroy_node()
