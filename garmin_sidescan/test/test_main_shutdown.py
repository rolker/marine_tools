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

import socket
import subprocess
import sys
import threading
import types
from unittest.mock import MagicMock, patch

import garmin_sidescan.node as node_mod
from garmin_sidescan.node import GarminSidescanNode, main
import pytest
import rclpy
from rclpy.context import Context
from rclpy.exceptions import InvalidHandle
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


# ----- a real SIGINT to the real entry point --------------------------------

# Mirrors the harness `sound_speed_bridge` and `zda_serial_bridge` carry: the
# console entry point is run for real in a subprocess with only its I/O mocked,
# because the behaviour under test *is* signal delivery -- an in-process test
# cannot reproduce rclpy's own signal handler tearing the context down while
# the executor is still inside spin(). The multicast receive sockets raise
# socket.timeout (the real exception class, which _rx_loop/_aux_loop treat as
# "no datagram this second"), so the driver's four worker threads run their
# genuine loops with no network. The TCP control socket is the same MagicMock,
# so the startup command and the shutdown transmit-OFF "succeed" without a GCV
# on the wire.
_SIGINT_HARNESS = """
import os
import signal
import socket
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import garmin_sidescan.node as node_mod


def _recvfrom(*args, **kwargs):
    time.sleep(0.05)
    raise socket.timeout()


def _fake_socket(*args, **kwargs):
    sock = MagicMock()
    sock.recvfrom.side_effect = _recvfrom
    sock.__enter__.return_value = sock
    sock.__exit__.return_value = False
    return sock


def _interrupt():
    time.sleep(3.0)
    os.kill(os.getpid(), signal.SIGINT)


threading.Thread(target=_interrupt, daemon=True).start()
with patch.object(node_mod.socket, 'socket', _fake_socket):
    sys.exit(node_mod.main())
"""


def test_sigint_exits_zero_without_a_traceback(tmp_path):
    """
    A real SIGINT to the console entry point exits 0 and prints no traceback.

    This node is the one where the callback-versus-shutdown race was actually
    observed, so the end-to-end contract -- Ctrl-C is exit 0, silently -- is
    pinned by execution and not only by the per-callback guard tests above.
    Both fixes are under test at once: ``main()``'s
    ``ExternalShutdownException``/``try_shutdown()`` handling, and the
    ``quiet_on_shutdown`` guard on the timer, subscription and thread entry
    points that keep running while the context is being torn down.
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


def test_invalid_handle_after_our_own_stop_is_quiet_even_on_a_live_context():
    """
    A worker that outlives the join publishes into a destroyed handle.

    super().destroy_node() has taken the publishers but rclpy is not down
    yet, so the context is live; the node's own stop event is what marks
    this as teardown rather than a fault.
    """
    ctx = _context(live=True)
    node = _status_node(ctx, InvalidHandle('publisher handle destroyed'))
    node._stop_event = threading.Event()
    node._stop_event.set()
    try:
        assert GarminSidescanNode._publish_status(node) is None
    finally:
        ctx.try_shutdown()


# ----- a refused start, and a constructor that fails part-way ---------------

def _fake_socket_factory():
    """Return a socket stand-in whose receives time out, so the loops run dry."""
    def _recvfrom(*_args, **_kwargs):
        raise socket.timeout()

    def _factory(*_args, **_kwargs):
        sock = MagicMock()
        sock.recvfrom.side_effect = _recvfrom
        sock.__enter__.return_value = sock
        sock.__exit__.return_value = False
        return sock

    return _factory


def _live_gcv_threads():
    """Names of this node's worker threads that are still running."""
    return sorted(t.name for t in threading.enumerate()
                  if t.name.startswith('gcv_') and t.is_alive())


def test_main_reports_a_wrong_typed_parameter_and_shuts_down():
    """
    An override of the wrong ROS type is a refused start, not a traceback.

    rclpy raises InvalidParameterTypeException at declaration, before the
    node builds anything; it must reach one FATAL line naming the cause and
    exit 1, so ros2 launch and systemd Restart=on-failure see a failure
    rather than a clean stop.
    """
    logger = MagicMock()
    try:
        with patch.object(node_mod.socket, 'socket', _fake_socket_factory()), \
                patch('garmin_sidescan.node.rclpy.logging.get_logger',
                      return_value=logger):
            with pytest.raises(SystemExit) as excinfo:
                main(args=['--ros-args', '-p', 'gcv_ip:=123'])
        assert excinfo.value.code == 1
        assert not rclpy.ok()
        assert logger.fatal.call_count == 1
        assert 'gcv_ip' in logger.fatal.call_args.args[0]
        assert not _live_gcv_threads()
    finally:
        rclpy.try_shutdown()


def test_a_constructor_failure_after_worker_start_leaves_no_live_threads():
    """
    A constructor that raises once workers are running must unwind them.

    main() does not call destroy_node() on a node whose constructor raised,
    so anything the constructor started is a daemon nobody will ever join --
    still publishing, still able to put a transmit command on the wire, on a
    node that does not exist. The failure is injected where the residual
    window actually is: partway through the start block itself.
    """
    starts = []
    real_start = threading.Thread.start

    def _start(self):
        starts.append(self.name)
        if self.name == 'gcv_config':
            raise RuntimeError("can't start new thread")
        return real_start(self)

    rclpy.init()
    try:
        with patch.object(node_mod.socket, 'socket', _fake_socket_factory()), \
                patch.object(threading.Thread, 'start', _start):
            with pytest.raises(RuntimeError, match="can't start new thread"):
                GarminSidescanNode()
        # The two that did start were stopped and joined before the raise.
        assert starts == ['gcv_rx', 'gcv_status', 'gcv_config']
        assert not _live_gcv_threads()
    finally:
        rclpy.try_shutdown()


def test_main_exits_one_with_one_fatal_on_a_constructor_value_error():
    """
    Our own validation failing takes the same path as rclpy's.

    Pinned separately from the wrong-typed override because it is the branch
    a future parameter check will land on, and because it is what proves the
    construction sits inside main()'s try: built outside it, the ValueError
    would escape as a traceback with rclpy never shut down.
    """
    logger = MagicMock()
    try:
        with patch('garmin_sidescan.node.GarminSidescanNode',
                   side_effect=ValueError('range_max_m must exceed range_min_m')), \
                patch('garmin_sidescan.node.rclpy.logging.get_logger',
                      return_value=logger):
            with pytest.raises(SystemExit) as excinfo:
                main()
        assert excinfo.value.code == 1
        assert not rclpy.ok()
        assert logger.fatal.call_count == 1
        assert 'range_max_m must exceed range_min_m' in logger.fatal.call_args.args[0]
    finally:
        rclpy.try_shutdown()
