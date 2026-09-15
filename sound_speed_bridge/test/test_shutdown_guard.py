"""
Shutdown-guard tests for the diagnostics timer (rolker/marine_tools#78).

``main()``'s exit-code contract is not enough on its own: rclpy's signal
handler tears the context down while the executor is still inside
``spin()``, so a timer callback can reach ``publish()`` after the
publisher's context has gone invalid. rcl raises ``RCLError: Failed to
publish: publisher's context is invalid``, ``spin()`` propagates it, and a
deliberate operator stop exits 1 with a traceback -- indistinguishable
from a crash under systemd ``Restart=on-failure``. ``garmin_sidescan``
carries the same guard (see its ``test_main_shutdown.py``); this node's
one publishing timer carries it inline.

The race is not reproducible on demand, so the callback is driven directly
against a **real** ``rclpy.Context`` that has been shut down -- not a mock
of ``rclpy.ok`` -- with the publish raising the exact rcl error the field
shows.
"""

from unittest.mock import MagicMock, patch

import pytest
import rclpy
from rclpy.context import Context
from rclpy.impl.implementation_singleton import rclpy_implementation as _rclpy
from sound_speed_bridge.node import SoundSpeedBridgeNode


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
