"""Tests for sound_speed_bridge.sinks."""

import math

from sound_speed_bridge.parsers import SoundSpeedReading
from sound_speed_bridge.sinks import (
    format_passthrough,
    format_template,
    format_valeport,
)


def _reading(
    value=1500.0,
    raw_mm_s=1500000,
    raw_bytes=b'1500.000\r',
    receive_time_ns=0,
):
    return SoundSpeedReading(
        sound_speed_m_s=value,
        raw_mm_s=raw_mm_s,
        raw_bytes=raw_bytes,
        receive_time_ns=receive_time_ns,
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


def test_passthrough_relays_raw_bytes_verbatim():
    """Passthrough emits exactly what the parser saw, with original terminator."""
    out = format_passthrough(_reading(raw_bytes=b'1500.123\r\r\n'))
    assert out == b'1500.123\r\r\n'


def test_passthrough_emits_even_on_nan():
    """Passthrough relays framed bytes regardless of parse outcome."""
    out = format_passthrough(_reading(value=float('nan'), raw_mm_s=None,
                                      raw_bytes=b'GARBAGE\r'))
    assert out == b'GARBAGE\r'


def test_passthrough_skips_when_raw_bytes_empty():
    """Truly empty raw_bytes (shouldn't happen in practice) yields no packet."""
    assert format_passthrough(_reading(raw_bytes=b'')) is None


def test_template_basic_format_with_int_mm_s():
    r"""Bit-exact int mm/s flows through {value_int_mm_s} unchanged.

    Templates arrive at the formatter already decoded by the node.
    """
    out = format_template(
        _reading(value=1500.123, raw_mm_s=1500123),
        template=' {value_int_mm_s:7d}\r\n',
    )
    assert out == b' 1500123\r\n'


def test_template_passes_through_control_chars():
    r"""Control chars in the (already-decoded) template flow through to bytes."""
    out = format_template(_reading(value=1500.0), template='{value:.1f}\n')
    assert out == b'1500.0\n'


def test_template_uses_frame_id_from_ctx():
    """Templates can interpolate {frame_id} from the node-supplied context."""
    out = format_template(
        _reading(value=1500.0),
        template='{frame_id}={value:.1f}',
        ctx={'frame_id': 'aml_svs_205937'},
    )
    assert out == b'aml_svs_205937=1500.0'


def test_template_skips_nan():
    """A NaN reading must not produce a templated packet."""
    out = format_template(
        _reading(value=float('nan'), raw_mm_s=None),
        template='{value:.3f}',
    )
    assert out is None


def test_template_skips_empty_template():
    """An empty template means the target was misconfigured; emit nothing."""
    assert format_template(_reading(), template='') is None


def test_template_returns_none_on_bad_substitution():
    """A template referencing an unknown variable is skipped, not crashed."""
    out = format_template(
        _reading(value=1500.0),
        template='{not_a_real_variable}',
    )
    assert out is None


def test_template_int_mm_s_falls_back_to_float_when_raw_none():
    """When raw_mm_s is None, value_int_mm_s is rounded from the float."""
    out = format_template(
        _reading(value=1500.5, raw_mm_s=None),
        template='{value_int_mm_s}',
    )
    assert out == b'1500500'
