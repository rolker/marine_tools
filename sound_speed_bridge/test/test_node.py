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
corrupts the line terminator — still reaches the bag.

``raw`` tests exercise :meth:`SoundSpeedBridgeNode._handle_reading`
directly with mocked serial I/O, following the
``zda_serial_bridge/test/test_node.py`` pattern; ``serial_tap`` tests must
drive :meth:`SoundSpeedBridgeNode._serial_loop` itself, via the
``_drive_serial_loop`` harness below, because the tap publish lives there.
"""

from unittest.mock import MagicMock, patch

from diagnostic_msgs.msg import DiagnosticArray
import pytest
import rclpy
from sound_speed_bridge.node import SoundSpeedBridgeNode
from sound_speed_bridge.parsers import RegexParser, SoundSpeedReading
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
        # Same contract for the pre-framing tap: a bare relative `serial_tap`
        # of type UInt8MultiArray, so a rename is caught here rather than by a
        # deployment bag that turns out to be missing the topic.
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


def _drive_serial_loop(node, mock_serial_cls, chunks, stop_before_index=None):
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
    node = _make_node(mock_serial_cls)
    try:
        node._tap_pub = MagicMock()
        chunk = b'1500.123\r\r\n'
        _drive_serial_loop(node, mock_serial_cls, [chunk])
        assert node._tap_pub.publish.call_count == 1
        msg = node._tap_pub.publish.call_args.args[0]
        assert bytes(msg.data) == chunk
    finally:
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_serial_tap_captures_unframeable_field_garbage(mock_serial_cls):
    """
    Corrupted field bytes that never frame still reach the bag.

    This is the whole point of #77: with the terminator corrupted, the parser
    yields no readings and `raw` publishes nothing, so before this topic
    existed the bag showed silence — indistinguishable from a dead probe.
    """
    node = _make_node(mock_serial_cls)
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
    node = _make_node(mock_serial_cls)
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
    node = _make_node(mock_serial_cls)
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
    node = _make_node(mock_serial_cls)
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
    node = _make_node(mock_serial_cls)
    try:
        node._tap_pub = MagicMock()
        node._tap_pub.publish.side_effect = RuntimeError('publisher destroyed')
        node._pub = MagicMock()
        chunks = [b'1500.123\r\r\n', b'1499.900\r\r\n']
        # Returns normally rather than propagating: both chunks are consumed.
        _drive_serial_loop(node, mock_serial_cls, chunks)
        assert node._tap_pub.publish.call_count == 2
        assert node._tap_error_count == 2
        # Bytes are only counted when they were actually published.
        assert node._tap_byte_count == 0
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
    node = _make_node(mock_serial_cls)
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
    node = _make_node(mock_serial_cls)
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
