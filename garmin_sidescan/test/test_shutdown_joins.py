"""
Thread-join tests for ``destroy_node()`` (#78, [SW5]).

``destroy_node()`` used to flip a stop flag, send transmit OFF and call
``super().destroy_node()`` without waiting for any of the driver's four
daemon threads. All four publish -- imagery, nadir range and water
temperature from ``_rx_loop``, the raw status/config captures from the two
``_aux_loop``s, transmit state from the startup thread -- so a thread still
running when the node's publishers were destroyed kept working against a
torn-down node, and the startup thread could still put a ``transmit ON`` on
the wire *after* the shutdown OFF, leaving the sonar pinging unattended.

The threads are daemons, so the fix has to bound what it waits for. These
tests pin both halves: every live thread is joined and gone, and a wedged
socket read costs a bounded interval rather than hanging the process.
"""

import contextlib
import socket
import threading
import time
import types
from unittest.mock import patch

from garmin_sidescan.commands import TRANSMIT_OFF
from garmin_sidescan.node import GarminSidescanNode, SHUTDOWN_JOIN_TIMEOUT_S
import pytest
import rclpy


@pytest.fixture(autouse=True)
def _ros_context():
    """Initialise rclpy for the duration of each test."""
    rclpy.init()
    yield
    rclpy.try_shutdown()


class _FakeSocket:
    """
    Multicast socket stand-in whose ``recvfrom`` never returns a payload.

    ``released`` is what a *wedged* read blocks on: left unset, the read
    outlasts the join budget the way a stuck kernel read would, and the test
    sets it at teardown so no daemon thread is left blocked for the rest of
    the session.
    """

    def __init__(self, wedged=False, released=None):
        self.wedged = wedged
        self.released = released
        self.closed = False

    def recvfrom(self, _bufsize):
        """Block (briefly, or past the join budget) and then time out."""
        if self.wedged:
            # Stands in for a read whose own 1.0 s socket timeout never fires.
            self.released.wait(30.0)
        else:
            # The real socket has settimeout(1.0); shorten it so the loops turn
            # over promptly under test without busy-spinning. Long enough that
            # a thread nobody joined is still demonstrably alive afterwards.
            time.sleep(0.25)
        raise socket.timeout()

    def close(self):
        """Record the close the receive loops perform on their way out."""
        self.closed = True


@contextlib.contextmanager
def _node(open_mcast, send_ok=True):
    """Build a real node with its sockets faked out, and yield it."""
    sends = []

    def _send(_self, data):
        sends.append(data)
        return send_ok

    with patch.object(GarminSidescanNode, '_open_mcast', open_mcast), \
            patch.object(GarminSidescanNode, '_send', _send):
        node = GarminSidescanNode()
        node._test_sends = sends
        yield node


def _alive(node):
    """Return the names of the node's worker threads that are still running."""
    threads = [node._rx_thread, *node._aux_threads, node._startup_thread]
    return [t.name for t in threads if t.is_alive()]


def test_destroy_node_joins_every_worker_thread():
    """Every thread that was alive is joined and gone once destroy_node returns."""
    with _node(lambda _self, _group, _port: _FakeSocket()) as node:
        # Asserted before the teardown so the check below cannot pass vacuously.
        assert _alive(node) == ['gcv_rx', 'gcv_status', 'gcv_config', 'gcv_startup']

        node.destroy_node()

        assert _alive(node) == []


def test_a_wedged_thread_does_not_hang_destroy_node():
    """A socket read that never returns costs the join budget, not the process."""
    released = threading.Event()

    def _open(_self, _group, _port):
        return _FakeSocket(wedged=True, released=released)

    with _node(_open) as node:
        try:
            time.sleep(0.2)
            assert 'gcv_rx' in _alive(node)

            started = time.monotonic()
            with patch.object(node, 'get_logger') as logger:
                node.destroy_node()
            elapsed = time.monotonic() - started

            # Bounded -- and bounded for the whole set, so three wedged receive
            # threads cost the budget once rather than three times.
            assert elapsed < SHUTDOWN_JOIN_TIMEOUT_S + 2.0
            # ...and it really waited, rather than skipping the join entirely.
            assert elapsed >= SHUTDOWN_JOIN_TIMEOUT_S - 0.5
            warned = ' '.join(str(c) for c in logger.return_value.warn.call_args_list)
            assert 'gcv_rx' in warned
        finally:
            released.set()


def test_a_reconnecting_thread_is_joined_promptly():
    """
    The reconnect back-off is interruptible, so a stopped thread leaves at once.

    A GCV that is unreachable at shutdown -- the ordinary case when the boat is
    being packed up -- parks every receive loop in its rejoin back-off. A plain
    ``time.sleep`` there would spend most of the join budget waiting for a
    thread that has nothing left to do.
    """
    def _open(_self, _group, _port):
        raise OSError('no route to the GCV')

    with _node(_open) as node:
        time.sleep(0.2)          # the loops are inside their 2.0 s back-off

        started = time.monotonic()
        node.destroy_node()
        elapsed = time.monotonic() - started

        assert _alive(node) == []
        assert elapsed < 1.0


def test_transmit_off_is_sent_after_every_thread_is_joined():
    """
    The shutdown OFF is the last command on the wire.

    Sent before the joins it could be overridden by the startup thread's own
    ``transmit_on_startup`` ON -- the failure that leaves a sonar pinging with
    nobody watching it.
    """
    alive_at_off = []

    with _node(lambda _self, _group, _port: _FakeSocket()) as node:
        def _send(data):
            if data == TRANSMIT_OFF:
                alive_at_off.append(_alive(node))
            return True

        with patch.object(node, '_send', _send):
            node.destroy_node()

        assert alive_at_off, 'destroy_node sent no transmit OFF'
        assert alive_at_off[-1] == []


def test_destroy_node_still_retries_a_failing_transmit_off():
    """The pre-existing OFF retry and its error survive the joins unchanged."""
    with _node(lambda _self, _group, _port: _FakeSocket(), send_ok=False) as node:
        with patch.object(node, 'get_logger') as logger:
            node.destroy_node()

        # Three shutdown retries, on top of whatever startup sent.
        assert node._test_sends[-3:] == [TRANSMIT_OFF] * 3
        errors = ' '.join(str(c) for c in logger.return_value.error.call_args_list)
        assert 'could not confirm transmit OFF' in errors


def test_startup_thread_issues_no_command_once_the_stop_is_signalled():
    """
    A shutdown mid-startup stops the startup thread issuing further commands.

    Without it the range command -- or a ``transmit_on_startup`` ON -- could
    land on the wire after ``destroy_node``'s OFF.
    """
    sent = []
    node = types.SimpleNamespace(
        _tx_lock=threading.Lock(),
        _stop_event=threading.Event(),
        _transmitting=False,
        _send=lambda data: sent.append(data) or True,
        _publish_tx_state=lambda: None,
        _set_transmit=lambda on, reason: sent.append(('set_transmit', on)),
        get_logger=lambda: types.SimpleNamespace(
            info=lambda *a, **k: None, error=lambda *a, **k: None),
    )
    node._stop_event.set()

    GarminSidescanNode._startup_transmit_state(node, True, 3, 25.0)

    # One OFF attempt, then the interruptible wait returns at once and the
    # thread returns: no range command, no startup ON.
    assert sent == [TRANSMIT_OFF]
