"""
Unit tests for the sensor-constant tables and ``_resolve_freq_bw`` helper.

These exercise the frequency / beamwidth resolution logic directly, without
constructing a ``rclpy`` node or spinning -- the helper is deliberately
ROS-free so the table behaviour (param-override, generation fallback, and the
unknown-generation case) is testable in isolation.
"""
import math

from garmin_sidescan.node import (
    _FREQ_HZ, _resolve_freq_bw, _RX_BEAMWIDTH_RAD, _TX_BEAMWIDTH_RAD)


def test_freq_beamwidth_table_coverage():
    """Every table entry is present, positive, and physically sane."""
    # All six frequency entries present and positive.
    for key in (('gcv20', 'port'), ('gcv20', 'stbd'), ('gcv20', 'down'),
                ('gcv10', 'port'), ('gcv10', 'stbd'), ('gcv10', 'down')):
        assert key in _FREQ_HZ
        assert _FREQ_HZ[key] > 0.0

    # Three rx (across-track) and three tx (along-track) GCV-20 entries.
    for key in (('gcv20', 'port'), ('gcv20', 'stbd'), ('gcv20', 'down')):
        rx = _RX_BEAMWIDTH_RAD[key]
        tx = _TX_BEAMWIDTH_RAD[key]
        # Positive, and a full -3 dB width must be below a half-turn.
        assert 0.0 < rx < math.pi
        assert 0.0 < tx < math.pi
        # The across-track receive fan is the WIDE beam; along-track is narrow.
        assert rx > tx

    # GCV-10 beamwidths are deliberately not populated (spec unconfirmed).
    assert ('gcv10', 'port') not in _RX_BEAMWIDTH_RAD
    assert ('gcv10', 'port') not in _TX_BEAMWIDTH_RAD


def test_table_fills_when_param_zero():
    """A zero freq param falls back to the generation table for freq + bw."""
    freq, rx, tx = _resolve_freq_bw('gcv20', 'port', 0.0)
    assert freq == 1_120_000.0
    assert rx == math.radians(55.0)
    assert tx == math.radians(0.44)

    # Down channel (ClearVü) too.
    freq, rx, tx = _resolve_freq_bw('gcv20', 'down', 0.0)
    assert freq == 820_000.0
    assert rx == math.radians(46.0)
    assert tx == math.radians(0.74)


def test_explicit_param_overrides_table():
    """A non-zero freq param wins over the table; beamwidths still fill."""
    freq, rx, tx = _resolve_freq_bw('gcv20', 'port', 500_000.0)
    assert freq == 500_000.0
    # Beamwidths are independent of the frequency override.
    assert rx == math.radians(55.0)
    assert tx == math.radians(0.44)


def test_unknown_generation_leaves_zero_freq_and_no_beamwidth():
    """With no latched generation, freq stays 0.0 and beamwidths are absent."""
    freq, rx, tx = _resolve_freq_bw(None, 'port', 0.0)
    assert freq == 0.0
    assert rx is None
    assert tx is None
