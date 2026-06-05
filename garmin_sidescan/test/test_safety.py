"""
Unit tests for the transmit-state safety rule.

The critical invariant: a transmit-OFF command whose TCP send FAILED must not
be recorded as OFF, or the watchdog would stop retrying and a dry transducer
could keep pinging while everything reports OFF.
"""
from garmin_sidescan.node import (
    range_in_bounds,
    transmit_state_after,
    watchdog_action,
)


def test_successful_on_is_transmitting():
    assert transmit_state_after(commanded_on=True, send_ok=True) is True


def test_failed_on_is_not_transmitting():
    assert transmit_state_after(commanded_on=True, send_ok=False) is False


def test_successful_off_is_not_transmitting():
    assert transmit_state_after(commanded_on=False, send_ok=True) is False


def test_failed_off_stays_transmitting():
    # the safety-critical case: OFF send failed -> assume still pinging
    assert transmit_state_after(commanded_on=False, send_ok=False) is True


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
