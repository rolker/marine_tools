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
"""

from unittest.mock import patch

from kongsberg_em_bridge.node import main
import rclpy
from rclpy.executors import ExternalShutdownException


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
