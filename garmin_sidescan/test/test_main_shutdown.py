"""
Shutdown-path tests for ``main()`` and the callbacks under it (#78).

A deliberate stop -- Ctrl-C, ``ros2 launch`` shutdown, systemd SIGINT --
must exit 0. rclpy's own signal handler shuts the context down before
``main()``'s ``finally`` runs, so ``spin()`` raises
``ExternalShutdownException`` (uncaught: exit 1 with a traceback) and a
plain ``rclpy.shutdown()`` then raises ``RCLError: rcl_shutdown already
called``. Under ``Restart=on-failure`` that made an operator's deliberate
stop look like a crash.

For ``main()`` the node itself is mocked out: what is under test is the
two lines of ``main()``, not the bridge.

The second half covers the layer below it. Fixing ``main()`` was not
enough for this node: the shutdown lands while the executor is still in
``spin()``, so the ``_reconcile_transmit_param`` timer callback reached
``set_parameters()`` -- and the parameter-event publish inside it -- on a
context that was already down, raising ``RCLError: Failed to publish:
publisher's context is invalid`` straight out of ``spin()``. Exit 1 with
a traceback again, from one layer in. Those tests drive the callbacks
directly with a genuinely shut-down context, since the race itself is not
reproducible on demand.
"""

import threading
import types
from unittest.mock import patch

from garmin_sidescan.node import GarminSidescanNode, main
import pytest
import rclpy
from rclpy.context import Context
from rclpy.executors import ExternalShutdownException
from rclpy.impl.implementation_singleton import rclpy_implementation as _rclpy


def _external_shutdown(_node):
    """Emulate rclpy's signal handler: shut the context, then raise."""
    rclpy.utilities.get_default_context().shutdown()
    raise ExternalShutdownException()


@patch('garmin_sidescan.node.GarminSidescanNode')
def test_main_returns_cleanly_on_an_external_shutdown(mock_node_cls):
    """main() returns normally and shuts down idempotently, without raising."""
    try:
        with patch('garmin_sidescan.node.rclpy.spin',
                   side_effect=_external_shutdown):
            main()  # must not raise
        assert not rclpy.ok()
        assert mock_node_cls.return_value.destroy_node.call_count == 1
    finally:
        rclpy.try_shutdown()


# ----- callbacks vs. a context that is already down ----------------------

# The exact rcl message the field shows when a publisher outlives its context.
_DEAD_CONTEXT = "Failed to publish: publisher's context is invalid"


def _context(live):
    """Return a real rclpy Context, initialised and optionally shut down."""
    ctx = Context()
    rclpy.init(context=ctx)
    if not live:
        ctx.shutdown()
    return ctx


class _FakeReconcileNode:
    """Stand-in exposing only what _reconcile_transmit_param touches."""

    def __init__(self, context, exc):
        self.context = context
        self._exc = exc
        self._params = {'transmit': types.SimpleNamespace(value=False)}
        self._transmitting = True            # disagrees -> the write is attempted
        self._tx_sync = False
        self._tx_lock = threading.Lock()
        self.attempts = 0
        self._control_server = None

    def has_parameter(self, name):
        return name in self._params

    def get_parameter(self, name):
        return self._params[name]

    def set_parameters(self, params):
        self.attempts += 1
        raise self._exc


def _status_node(context, exc):
    """Stand-in exposing only what _publish_status touches."""
    def _raise(_msg):
        raise exc

    return types.SimpleNamespace(
        context=context, _transmitting=False, _sound_speed=1500.0,
        _ping_count={'port': 0, 'stbd': 0, 'down': 0},
        _pub_status=types.SimpleNamespace(publish=_raise),
        _publish_control_set=lambda: None)


def test_reconcile_is_quiet_once_the_context_is_shut_down():
    """
    The timer callback must not raise after the context has gone.

    This is the defect a deliberate SIGINT hit: rclpy's signal handler shuts
    the context down while spin() is still dispatching, the reconcile write's
    parameter-event publish then fails, and the RCLError propagates out of
    spin() -- exit 1 with a traceback for a clean operator stop.
    """
    ctx = _context(live=False)
    node = _FakeReconcileNode(ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    try:
        assert GarminSidescanNode._reconcile_transmit_param(node) is None
        assert node.attempts == 1          # guarded around the call, not before it
        assert node._tx_sync is False      # the finally still reset the guard
    finally:
        ctx.try_shutdown()


def test_a_publish_timer_is_quiet_once_the_context_is_shut_down():
    """The sibling publishing timers carry the same guard, not just reconcile."""
    ctx = _context(live=False)
    node = _status_node(ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    try:
        assert GarminSidescanNode._publish_status(node) is None
    finally:
        ctx.try_shutdown()


def test_a_publish_failure_on_a_live_context_is_still_raised():
    """
    A real fault must stay loud: the guard is not a blanket except.

    Only a failure with the context already down is a shutdown race; the same
    RCLError while the node is running is a genuine fault, and swallowing it
    would hide a publisher that has stopped working mid-deployment.
    """
    ctx = _context(live=True)
    node = _status_node(ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    try:
        with pytest.raises(_rclpy.RCLError):
            GarminSidescanNode._publish_status(node)
    finally:
        ctx.try_shutdown()


def test_a_non_rcl_error_is_not_swallowed_by_the_shutdown_guard():
    """A bug in a callback body still surfaces, shut-down context or not."""
    ctx = _context(live=False)
    node = _status_node(ctx, ValueError('a bug in the callback'))
    try:
        with pytest.raises(ValueError, match='a bug in the callback'):
            GarminSidescanNode._publish_status(node)
    finally:
        ctx.try_shutdown()
