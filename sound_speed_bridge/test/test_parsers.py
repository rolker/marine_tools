"""Tests for sound_speed_bridge.parsers."""

import math

from sound_speed_bridge.parsers import AMLParser, SoundSpeedReading


def _readings(parser, data, t=12345):
    return list(parser.feed(data, t))


def test_aml_basic_sentence():
    """A single well-formed AML sentence yields one reading."""
    p = AMLParser()
    readings = _readings(p, b'1500.123\r\r\n')
    assert len(readings) == 1
    r = readings[0]
    assert isinstance(r, SoundSpeedReading)
    assert r.sound_speed_m_s == 1500.123
    assert r.raw_mm_s == 1500123
    assert r.raw_bytes == b'1500.123\r'
    assert r.receive_time_ns == 12345


def test_aml_bit_exact_avoids_float_drift():
    """
    raw_mm_s must derive from the source decimal string, not from float math.

    The PowerShell stand-in deliberately used [decimal] to avoid IEEE-754
    rounding on the m/s -> mm/s conversion. AMLParser uses Decimal for the
    same reason; this test asserts the integer is exact.
    """
    p = AMLParser()
    readings = _readings(p, b'1500.999\r\r\n')
    assert readings[0].raw_mm_s == 1500999


def test_aml_handles_crcrlf_padding_between_sentences():
    r"""
    Stray newline bytes between sentences should not produce extra readings.

    Each AML sentence ends with CRCRLF; framing on the first CR leaves a
    trailing \n that the parser must silently skip.
    """
    p = AMLParser()
    readings = _readings(p, b'1500.000\r\r\n1500.500\r\r\n')
    values = [r.sound_speed_m_s for r in readings]
    raw_mm_s = [r.raw_mm_s for r in readings]
    assert values == [1500.0, 1500.5]
    assert raw_mm_s == [1500000, 1500500]


def test_aml_partial_buffering():
    """A sentence split across two feed() calls should still parse as one reading."""
    p = AMLParser()
    first = _readings(p, b'1500.')
    assert first == []
    second = _readings(p, b'123\r\r\n')
    assert len(second) == 1
    assert second[0].sound_speed_m_s == 1500.123


def test_aml_unparseable_yields_nan_reading():
    """
    A framed but garbled sentence yields a NaN reading with raw bytes preserved.

    Passthrough sinks rely on raw_bytes still being present so they can relay
    garbage downstream for inspection.
    """
    p = AMLParser()
    readings = _readings(p, b'GARBAGE\r\r\n')
    assert len(readings) == 1
    r = readings[0]
    assert math.isnan(r.sound_speed_m_s)
    assert r.raw_mm_s is None
    assert r.raw_bytes == b'GARBAGE\r'


def test_aml_skips_empty_lines():
    """Stray CRs that frame an empty line should not yield a reading."""
    p = AMLParser()
    readings = _readings(p, b'\r\r\n\r\r\n1500.000\r\r\n')
    values = [r.sound_speed_m_s for r in readings]
    assert values == [1500.0]
