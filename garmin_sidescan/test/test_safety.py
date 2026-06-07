"""
Unit tests for the transmit-state safety rule.

The critical invariant: a transmit-OFF command whose TCP send FAILED must not
be recorded as OFF, or the watchdog would stop retrying and a dry transducer
could keep pinging while everything reports OFF.
"""
import types

from garmin_sidescan.node import (
    GarminSidescanNode,
    range_in_bounds,
    transmit_state_after,
    watchdog_action,
)


def test_successful_on_is_transmitting():
    assert transmit_state_after(commanded_on=True, send_ok=True, prior=False) is True


def test_failed_on_from_off_stays_off():
    # ON requested from a known-off state, send failed -> still off
    assert transmit_state_after(commanded_on=True, send_ok=False, prior=False) is False


def test_failed_on_while_possibly_pinging_stays_transmitting():
    # safety-critical: a prior failed OFF left prior=True (may still be pinging);
    # an auto-resume ON whose send also fails must NOT report OFF and disarm the
    # watchdog over a live, dry transducer.
    assert transmit_state_after(commanded_on=True, send_ok=False, prior=True) is True


def test_successful_off_is_not_transmitting():
    assert transmit_state_after(commanded_on=False, send_ok=True, prior=True) is False


def test_failed_off_stays_transmitting():
    # the safety-critical case: OFF send failed -> assume still pinging
    assert transmit_state_after(commanded_on=False, send_ok=False, prior=False) is True


# ----- watchdog decision (watchdog_action) -------------------------------

def _wd(**kw):
    base = {'safety_enabled': True, 'transmitting': True, 'has_sv_topic': True,
            'require_sv': True, 'sv_age': 0.0, 'sv_timeout': 5.0}
    base.update(kw)
    return watchdog_action(**base)


def test_watchdog_idle_when_safety_disabled():
    assert _wd(safety_enabled=False) == (False, '')


def test_watchdog_idle_when_not_transmitting():
    assert _wd(transmitting=False) == (False, '')


def test_watchdog_fresh_reading_no_action():
    assert _wd(sv_age=1.0) == (False, '')


def test_watchdog_stale_reading_stops():
    assert _wd(sv_age=9.0) == (True, 'stale')


def test_watchdog_never_received_stops():
    assert _wd(sv_age=None) == (True, 'stale')


def test_watchdog_no_source_with_require_sv_stops():
    # require_sv + no sound-speed source while (maybe) transmitting: the guard
    # refuses to start here, so the watchdog must stop rather than dry-ping.
    assert _wd(has_sv_topic=False, require_sv=True) == (True, 'no_source')


def test_watchdog_no_source_without_require_sv_leaves_transmit():
    # bench testing (require_sound_speed:=false): nothing to evaluate, no stop
    assert _wd(has_sv_topic=False, require_sv=False) == (False, '')


# ----- range bounds (range_in_bounds) ------------------------------------

def test_range_in_bounds_inclusive_edges():
    assert range_in_bounds(1.0, 1.0, 60.0)
    assert range_in_bounds(60.0, 1.0, 60.0)
    assert range_in_bounds(30.0, 1.0, 60.0)


def test_range_below_min_rejected():
    assert not range_in_bounds(0.5, 1.0, 60.0)


def test_range_above_max_rejected():
    assert not range_in_bounds(100.0, 1.0, 60.0)


# ----- _on_param_set batch atomicity (#19) -------------------------------

class _FakeLogger:
    """No-op logger so _on_param_set can run without a real node."""

    def warn(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass


class _FakeNode:
    """Minimal stand-in exposing only the attributes _on_param_set touches."""

    def __init__(self, send_ok=True, static_params=()):
        self._send_ok = send_ok
        self.sends = []
        self._static = set(static_params)
        self._range_min = 1.0
        self._range_max = 60.0
        self._controls = {}
        self._safety_enabled = True
        self.publishes = 0

    def get_logger(self):
        return _FakeLogger()

    def _send(self, data):
        self.sends.append(data)
        return self._send_ok

    def has_parameter(self, name):
        return name in self._static

    def _publish_control_set(self):
        self.publishes += 1


def _param(name, value):
    return types.SimpleNamespace(name=name, value=value)


def _on_param_set(node, params):
    return GarminSidescanNode._on_param_set(node, params)


def test_valid_range_alone_sends_and_succeeds():
    node = _FakeNode()
    result = _on_param_set(node, [_param('range_m', 30.0)])
    assert result.successful is True
    assert len(node.sends) == 1
    assert node._controls['range'] == '30.0'


def test_batch_rejects_static_param_without_sending_range():
    # The #19 case: a valid range_m bundled with a startup-static param must be
    # rejected atomically -- the range command must NOT have been sent and the
    # UI mirror must NOT have been updated, or the param store desyncs from the
    # hardware/UI.
    node = _FakeNode(static_params=('sv_min',))
    result = _on_param_set(
        node, [_param('range_m', 30.0), _param('sv_min', 1400.0)])
    assert result.successful is False
    assert node.sends == []
    assert 'range' not in node._controls


def test_batch_rejection_independent_of_param_order():
    # Same as above but static param first: the validate-all pre-pass must catch
    # it regardless of ordering, still without sending the range command.
    node = _FakeNode(static_params=('sv_min',))
    result = _on_param_set(
        node, [_param('sv_min', 1400.0), _param('range_m', 30.0)])
    assert result.successful is False
    assert node.sends == []


def test_range_send_failure_rejects_without_mirroring():
    node = _FakeNode(send_ok=False)
    result = _on_param_set(node, [_param('range_m', 30.0)])
    assert result.successful is False
    assert len(node.sends) == 1          # attempted
    assert 'range' not in node._controls  # but not mirrored on failure
