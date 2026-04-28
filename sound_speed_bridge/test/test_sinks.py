"""Tests for sound_speed_bridge.sinks."""

import math

from sound_speed_bridge.parsers import SoundSpeedReading
from sound_speed_bridge.sinks import format_valeport


def _reading(value=1500.0, raw_mm_s=1500000, raw_bytes=b'1500.000\r'):
    return SoundSpeedReading(
        sound_speed_m_s=value,
        raw_mm_s=raw_mm_s,
        raw_bytes=raw_bytes,
        receive_time_ns=0,
    )


def test_valeport_byte_exact_format():
    r"""
    The Valeport packet is ' NNNNNNN\r\n' with a leading space and 7-digit int mm/s.

    Any deviation breaks M3's built-in Valeport UDP parser.
    """
    out = format_valeport(_reading(value=1500.123, raw_mm_s=1500123))
    assert out == b' 1500123\r\n'


def test_valeport_uses_raw_mm_s_when_present():
    """raw_mm_s is the bit-exact integer; the formatter must use it directly."""
    out = format_valeport(_reading(value=1.0, raw_mm_s=1500999))
    assert out == b' 1500999\r\n'


def test_valeport_falls_back_to_rounded_float():
    """When raw_mm_s is None (e.g. RegexParser), round from the float."""
    out = format_valeport(_reading(value=1500.5, raw_mm_s=None))
    assert out == b' 1500500\r\n'


def test_valeport_skips_nan():
    """A NaN reading must not produce a packet."""
    out = format_valeport(_reading(value=float('nan'), raw_mm_s=None))
    assert out is None


def test_valeport_skips_negative_or_too_large():
    """Out-of-band integer mm/s must be skipped, not emitted as a malformed packet."""
    assert format_valeport(_reading(value=-1.0, raw_mm_s=-1000)) is None
    assert format_valeport(_reading(value=1.0e6, raw_mm_s=10_000_000)) is None


def test_valeport_zero_emits_packet():
    """0 mm/s is non-physical but is a valid 7-digit field; emit and let diagnostics flag it."""
    out = format_valeport(_reading(value=0.0, raw_mm_s=0))
    assert out == b'       0\r\n'
    assert len(out) == 10


def test_valeport_nan_with_only_float_path():
    """Sanity check on the NamedTuple constructor: NaN propagates."""
    assert math.isnan(_reading(value=float('nan'), raw_mm_s=None).sound_speed_m_s)
