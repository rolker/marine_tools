"""
Unit tests for the transmit-state safety rule.

The critical invariant: a transmit-OFF command whose TCP send FAILED must not
be recorded as OFF, or the watchdog would stop retrying and a dry transducer
could keep pinging while everything reports OFF.
"""
from garmin_sidescan.node import transmit_state_after


def test_successful_on_is_transmitting():
    assert transmit_state_after(commanded_on=True, send_ok=True) is True


def test_failed_on_is_not_transmitting():
    assert transmit_state_after(commanded_on=True, send_ok=False) is False


def test_successful_off_is_not_transmitting():
    assert transmit_state_after(commanded_on=False, send_ok=True) is False


def test_failed_off_stays_transmitting():
    # the safety-critical case: OFF send failed -> assume still pinging
    assert transmit_state_after(commanded_on=False, send_ok=False) is True
