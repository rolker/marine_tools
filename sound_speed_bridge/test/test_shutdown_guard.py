"""
Shutdown-guard tests for this node's two guarded call sites (#78).

``main()``'s exit-code contract is not enough on its own: rclpy's signal
handler tears the context down while the executor is still inside
``spin()``, so a timer callback can reach ``publish()`` after the
publisher's context has gone invalid. rcl raises ``RCLError: Failed to
publish: publisher's context is invalid``, ``spin()`` propagates it, and a
deliberate operator stop exits 1 with a traceback -- indistinguishable
from a crash under systemd ``Restart=on-failure``. ``garmin_sidescan``
carries the same guard as a decorator (see its ``test_main_shutdown.py``);
this node has two such call sites and carries the guard inline at each:
the diagnostics timer, and the reading path on the serial thread, where
an escaping RCLError ends the reader thread outright.

The race is not reproducible on demand, so each guarded call is driven
directly against a **real** ``rclpy.Context`` that has been shut down --
not a mock of ``rclpy.ok`` -- with the publish raising the exact rcl error
the field shows.
"""

from unittest.mock import MagicMock, patch

import pytest
import rclpy
from rclpy.context import Context
from rclpy.exceptions import InvalidHandle
from rclpy.impl.implementation_singleton import rclpy_implementation as _rclpy
from sound_speed_bridge.node import SoundSpeedBridgeNode
from sound_speed_bridge.parsers import SoundSpeedReading


# The exact rcl message a publisher that outlived its context raises.
_DEAD_CONTEXT = "Failed to publish: publisher's context is invalid"


@pytest.fixture(autouse=True)
def _ros_context():
    """Initialize and tear down the default context around each test."""
    rclpy.init()
    yield
    rclpy.shutdown()


def _context(live):
    """Return a real rclpy Context, initialized and optionally shut down."""
    ctx = Context()
    rclpy.init(context=ctx)
    if not live:
        ctx.shutdown()
    return ctx


class _ContextProxy:
    """
    A real node with ``context`` and ``_diag_pub`` overridden.

    ``_publish_diagnostics`` reads a dozen pieces of node state, so the
    stand-in delegates everything to a genuinely constructed node and
    replaces only the two things under test: which context decides
    "shutting down", and a publisher that raises.
    """

    def __init__(self, node, context, exc):
        object.__setattr__(self, '_node', node)
        object.__setattr__(self, 'context', context)
        pub = MagicMock()
        pub.publish.side_effect = exc
        object.__setattr__(self, '_diag_pub', pub)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_node'), name)


def _node(mock_serial_cls):
    """Build a real node with serial mocked out and its reader thread stopped."""
    port = MagicMock()
    port.read.return_value = b''
    mock_serial_cls.return_value.__enter__.return_value = port
    node = SoundSpeedBridgeNode()
    node._stop_event.set()
    node._serial_thread.join(timeout=2.0)
    return node


@patch('sound_speed_bridge.node.serial.Serial')
def test_diagnostics_is_quiet_once_the_context_is_shut_down(mock_serial_cls):
    """The 1 Hz diagnostics publish must not raise after the context has gone."""
    node = _node(mock_serial_cls)
    ctx = _context(live=False)
    proxy = _ContextProxy(node, ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    try:
        assert SoundSpeedBridgeNode._publish_diagnostics(proxy) is None
        # Guarded around the call, not before it: a pre-check would be
        # check-then-act with the shutdown free to land in the gap.
        assert proxy._diag_pub.publish.call_count == 1
    finally:
        ctx.try_shutdown()
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_a_publish_failure_on_a_live_context_is_still_raised(mock_serial_cls):
    """
    A real fault stays loud: the guard is not a blanket except.

    Only a failure with the context already down is a shutdown race. The
    same RCLError while the node is running means the diagnostics publisher
    has stopped working mid-deployment, and swallowing it would hide that.
    """
    node = _node(mock_serial_cls)
    ctx = _context(live=True)
    proxy = _ContextProxy(node, ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    try:
        with pytest.raises(_rclpy.RCLError):
            SoundSpeedBridgeNode._publish_diagnostics(proxy)
    finally:
        ctx.try_shutdown()
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_a_non_rcl_error_is_not_swallowed_by_the_shutdown_guard(mock_serial_cls):
    """A bug in the callback body still surfaces, shut-down context or not."""
    node = _node(mock_serial_cls)
    ctx = _context(live=False)
    proxy = _ContextProxy(node, ctx, ValueError('a bug in the callback'))
    try:
        with pytest.raises(ValueError, match='a bug in the callback'):
            SoundSpeedBridgeNode._publish_diagnostics(proxy)
    finally:
        ctx.try_shutdown()
        node.destroy_node()


# ----- the reading path: the same guard, on the serial thread ---------------

class _ReadingProxy:
    """
    A real node with ``context`` and the ``sound_speed`` publisher overridden.

    Same shape as :class:`_ContextProxy`, for the other guarded call site.
    ``_handle_reading`` and ``_publish_reading`` are redefined to re-enter
    the unbound methods *with the proxy as self*: plain attribute delegation
    would hand back the underlying node's bound methods, and the guard would
    then consult the node's live context and publish on the node's real
    publisher instead of the raising stand-in.
    """

    def __init__(self, node, context, exc):
        object.__setattr__(self, '_node', node)
        object.__setattr__(self, 'context', context)
        pub = MagicMock()
        pub.publish.side_effect = exc
        object.__setattr__(self, '_pub', pub)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_node'), name)

    def _handle_reading(self, reading):
        return SoundSpeedBridgeNode._handle_reading(self, reading)

    def _publish_reading(self, reading):
        return SoundSpeedBridgeNode._publish_reading(self, reading)


def _reading():
    """One valid reading, as the parser would yield it."""
    return SoundSpeedReading(
        sound_speed_m_s=1500.5,
        receive_time_ns=1_000_000_000,
        raw_bytes=b'1500.500\r',
        raw_mm_s=1500500,
    )


@patch('sound_speed_bridge.node.serial.Serial')
def test_a_reading_publish_is_quiet_once_the_context_is_shut_down(mock_serial_cls):
    """
    The serial thread's sound_speed publish must not raise after teardown.

    The thread's own ``_stop_event`` test is check-then-act: a SIGINT can
    land between it and the publish, and the resulting RCLError has nothing
    to catch it on that thread (``_serial_loop`` catches only
    ``SerialException``/``OSError``), so it ends the reader as a thread
    traceback on a deliberate Ctrl-C.
    """
    node = _node(mock_serial_cls)
    ctx = _context(live=False)
    proxy = _ReadingProxy(node, ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    try:
        proxy._stop_event.clear()
        assert SoundSpeedBridgeNode._handle_reading(proxy, _reading()) is None
        # Guarded around the call, not before it.
        assert proxy._pub.publish.call_count == 1
    finally:
        node._stop_event.set()
        ctx.try_shutdown()
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_a_reading_publish_failure_on_a_live_context_is_still_raised(mock_serial_cls):
    """A publisher that stops working mid-deployment is still loud."""
    node = _node(mock_serial_cls)
    ctx = _context(live=True)
    proxy = _ReadingProxy(node, ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    try:
        proxy._stop_event.clear()
        with pytest.raises(_rclpy.RCLError):
            SoundSpeedBridgeNode._handle_reading(proxy, _reading())
    finally:
        node._stop_event.set()
        ctx.try_shutdown()
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_a_non_rcl_error_on_the_reading_path_is_not_swallowed(mock_serial_cls):
    """A bug in the publish body still surfaces, shut-down context or not."""
    node = _node(mock_serial_cls)
    ctx = _context(live=False)
    proxy = _ReadingProxy(node, ctx, ValueError('a bug on the reading path'))
    try:
        proxy._stop_event.clear()
        with pytest.raises(ValueError, match='a bug on the reading path'):
            SoundSpeedBridgeNode._handle_reading(proxy, _reading())
    finally:
        node._stop_event.set()
        ctx.try_shutdown()
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_the_serial_loop_survives_a_shutdown_race_on_a_publish(mock_serial_cls):
    """
    Forced ordering: the reader thread outlives a teardown mid-publish.

    The race is not reproducible on demand, so the ordering is forced --
    the context is already down and the publisher raises the exact rcl
    error -- and :meth:`_serial_loop` is run synchronously on the test
    thread over several chunks. Every chunk must be consumed: a loop that
    exits early is the field failure (silent sensor, ``serial_connected``
    still true) this guard exists to prevent.
    """
    node = _node(mock_serial_cls)
    ctx = _context(live=False)
    proxy = _ReadingProxy(node, ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    chunks = [b'1500.100\r\r\n', b'1500.200\r\r\n', b'1500.300\r\r\n']
    remaining = list(chunks)
    port = MagicMock()

    def _read(_size):
        if not remaining:
            proxy._stop_event.set()
            return b''
        return remaining.pop(0)

    port.read.side_effect = _read
    mock_serial_cls.return_value.__enter__.return_value = port
    try:
        proxy._stop_event.clear()
        SoundSpeedBridgeNode._serial_loop(proxy)   # must not raise
        assert not remaining, 'the reader loop died on the first publish'
        assert proxy._pub.publish.call_count == len(chunks)
    finally:
        node._stop_event.set()
        ctx.try_shutdown()
        node.destroy_node()


@patch('sound_speed_bridge.node.serial.Serial')
def test_a_late_reading_after_our_own_stop_is_quiet_on_a_live_context(mock_serial_cls):
    """
    A late read after our own stop must not end the reader with a traceback.

    A read that outlives destroy_node()'s 2 s join publishes into a
    destroyed handle while the context is still live; the stop event marks
    it as teardown rather than a fault.

    The stop is tripped *inside* the publish, not before the call: setting
    it beforehand would return at the handler's own check-then-act test and
    never reach the guard, so the guard's stop-event clause would go
    unexercised. Here the handler enters running, the publisher sets the
    stop event and then raises InvalidHandle -- exactly the order
    destroy_node() produces -- and only the guard can keep it quiet.
    """
    node = _node(mock_serial_cls)
    ctx = _context(live=True)
    proxy = _ReadingProxy(node, ctx, InvalidHandle('publisher handle destroyed'))

    def _stop_then_raise(_msg):
        node._stop_event.set()
        raise InvalidHandle('publisher handle destroyed')

    proxy._pub.publish.side_effect = _stop_then_raise
    try:
        proxy._stop_event.clear()
        assert SoundSpeedBridgeNode._handle_reading(proxy, _reading()) is None
        # The handler ran past its own pre-check and into the publish; the
        # guard, not the pre-check, is what absorbed the failure.
        assert proxy._pub.publish.call_count == 1
        assert node._stop_event.is_set()
    finally:
        node._stop_event.set()
        ctx.try_shutdown()
        node.destroy_node()
