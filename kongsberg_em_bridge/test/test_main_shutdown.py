# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Shutdown-path test for ``main()`` (rolker/marine_tools#78).

A deliberate stop -- Ctrl-C, ``ros2 launch`` shutdown, systemd SIGINT --
must exit 0. rclpy's own signal handler shuts the context down before
``main()``'s ``finally`` runs, so ``spin()`` raises
``ExternalShutdownException`` (uncaught: exit 1 with a traceback) and a
plain ``rclpy.shutdown()`` then raises ``RCLError: rcl_shutdown already
called``. Under ``Restart=on-failure`` that made an operator's deliberate
stop look like a crash.

The node itself is mocked out: what is under test is the two lines of
``main()``, not the bridge.

The second half covers the layer below it. Fixing ``main()`` is not enough
on its own: the shutdown lands while the executor is still inside
``spin()``, so the SonarInfo heartbeat timer can reach ``publish()`` after
the publisher's context has gone invalid -- ``RCLError: Failed to publish:
publisher's context is invalid`` straight out of ``spin()``, exit 1 with a
traceback from one layer in. That race is not reproducible on demand, so
the callback is driven directly against a **real** ``rclpy.Context`` that
has been shut down, with the publish raising the exact rcl error the field
shows. ``garmin_sidescan`` carries the same guard as a decorator.
"""

import threading
import types
from unittest.mock import MagicMock, patch

from kongsberg_em_bridge.node import KongsbergEmBridge, main
import pytest
import rclpy
from rclpy.context import Context
from rclpy.executors import ExternalShutdownException
from rclpy.impl.implementation_singleton import rclpy_implementation as _rclpy


def _external_shutdown(_node):
    """Emulate rclpy's signal handler: shut the context, then raise."""
    rclpy.utilities.get_default_context().shutdown()
    raise ExternalShutdownException()


@patch('kongsberg_em_bridge.node.KongsbergEmBridge')
def test_main_returns_cleanly_on_an_external_shutdown(mock_node_cls):
    """main() returns normally and shuts down idempotently, without raising."""
    try:
        with patch('kongsberg_em_bridge.node.rclpy.spin',
                   side_effect=_external_shutdown):
            main()  # must not raise
        assert not rclpy.ok()
        assert mock_node_cls.return_value.destroy_node.call_count == 1
    finally:
        rclpy.try_shutdown()


# ----- the heartbeat timer vs. a context that is already down ------------

# The exact rcl message a publisher that outlived its context raises.
_DEAD_CONTEXT = "Failed to publish: publisher's context is invalid"


def _context(live):
    """Return a real rclpy Context, initialized and optionally shut down."""
    ctx = Context()
    rclpy.init(context=ctx)
    if not live:
        ctx.shutdown()
    return ctx


def _heartbeat_node(context, exc):
    """Stand-in exposing only what ``_sonar_info_heartbeat`` touches."""
    pub = MagicMock()
    pub.publish.side_effect = exc
    return types.SimpleNamespace(
        context=context,
        _sonar_info_lock=threading.Lock(),
        _last_sonar_info=object(),      # a ping has been decoded
        sonar_info_pub=pub,
        get_logger=MagicMock(),
    )


def test_heartbeat_is_quiet_once_the_context_is_shut_down():
    """The heartbeat publish must not raise after the context has gone."""
    ctx = _context(live=False)
    node = _heartbeat_node(ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    try:
        assert KongsbergEmBridge._sonar_info_heartbeat(node) is None
        # Guarded around the call, not before it: a pre-check would be
        # check-then-act with the shutdown free to land in the gap.
        assert node.sonar_info_pub.publish.call_count == 1
        # A shutdown in flight is not a fault, so it is not logged either.
        assert node.get_logger.return_value.warning.call_count == 0
    finally:
        ctx.try_shutdown()


def test_a_publish_failure_on_a_live_context_is_still_raised():
    """
    A real fault stays loud: the guard is not a blanket except.

    Only a failure with the context already down is a shutdown race. The
    same RCLError while the node is running means the SonarInfo publisher
    has stopped working mid-survey, and swallowing it would hide that --
    which the previous blanket ``except Exception`` did.
    """
    ctx = _context(live=True)
    node = _heartbeat_node(ctx, _rclpy.RCLError(_DEAD_CONTEXT))
    try:
        with pytest.raises(_rclpy.RCLError):
            KongsbergEmBridge._sonar_info_heartbeat(node)
    finally:
        ctx.try_shutdown()


def test_a_non_rcl_error_is_still_warned_and_not_propagated():
    """
    A non-RCL bug keeps the previous best-effort behaviour.

    The heartbeat is a bag-segment convenience, so an unexpected exception
    in it is throttled-warned rather than allowed to kill the node; only
    the RCL class is triaged against the context.
    """
    ctx = _context(live=False)
    node = _heartbeat_node(ctx, ValueError('a bug in the callback'))
    try:
        assert KongsbergEmBridge._sonar_info_heartbeat(node) is None
        assert node.get_logger.return_value.warning.call_count == 1
    finally:
        ctx.try_shutdown()
