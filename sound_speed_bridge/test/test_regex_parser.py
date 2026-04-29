"""Tests for sound_speed_bridge.parsers.RegexParser."""

import math

import pytest

from sound_speed_bridge.parsers import RegexParser


def _readings(parser, data, t=12345):
    return list(parser.feed(data, t))


def test_regex_basic_capture():
    """A basic regex with a sound_speed group parses to a reading."""
    p = RegexParser(r'(?P<sound_speed>[0-9.]+)', line_terminator='lf')
    readings = _readings(p, b'1500.123\n')
    assert len(readings) == 1
    assert readings[0].sound_speed_m_s == 1500.123
    assert readings[0].raw_mm_s is None  # regex parser does not preserve int mm/s
    assert readings[0].raw_bytes == b'1500.123\n'


def test_regex_scale_for_mm_s_sensors():
    """Sensors that emit mm/s integers can be normalized via sound_speed_scale=0.001."""
    p = RegexParser(
        r'(?P<sound_speed>[0-9]+)',
        sound_speed_scale=0.001,
        line_terminator='crlf',
    )
    readings = _readings(p, b'1500123\r\n')
    assert readings[0].sound_speed_m_s == pytest.approx(1500.123)


def test_regex_optional_temperature_group():
    """Named temperature group, when matched, populates temperature_c."""
    pattern = r'SS=(?P<sound_speed>[\d.]+),T=(?P<temperature>[\d.-]+)'
    p = RegexParser(pattern, line_terminator='cr')
    readings = _readings(p, b'SS=1500.123,T=12.5\r')
    assert readings[0].temperature_c == 12.5
    assert readings[0].pressure_pa is None


def test_regex_optional_pressure_group():
    """Named pressure group, when matched, populates pressure_pa."""
    pattern = r'(?P<sound_speed>[\d.]+),P=(?P<pressure>[\d.-]+)'
    p = RegexParser(pattern, line_terminator='cr')
    readings = _readings(p, b'1500.0,P=101325\r')
    assert readings[0].pressure_pa == 101325.0


def test_regex_no_match_yields_nan():
    """A line that doesn't match the regex yields a NaN reading with raw_bytes set."""
    p = RegexParser(r'(?P<sound_speed>[0-9.]+)', line_terminator='lf')
    readings = _readings(p, b'nonsense\n')
    assert len(readings) == 1
    assert math.isnan(readings[0].sound_speed_m_s)
    assert readings[0].raw_bytes == b'nonsense\n'


def test_regex_partial_buffering_lf():
    """Sentence split across feeds parses on completion."""
    p = RegexParser(r'(?P<sound_speed>[0-9.]+)', line_terminator='lf')
    assert _readings(p, b'1500.') == []
    second = _readings(p, b'5\n')
    assert len(second) == 1
    assert second[0].sound_speed_m_s == 1500.5


def test_regex_partial_buffering_crlf():
    """CRLF terminator works correctly across split feeds."""
    p = RegexParser(r'(?P<sound_speed>[0-9.]+)', line_terminator='crlf')
    assert _readings(p, b'1500.5\r') == []
    second = _readings(p, b'\n')
    assert len(second) == 1
    assert second[0].sound_speed_m_s == 1500.5


def test_regex_rejects_pattern_without_sound_speed_group():
    """A pattern lacking the required named group is rejected at construction."""
    with pytest.raises(ValueError, match='sound_speed'):
        RegexParser(r'(\d+)', line_terminator='lf')


def test_regex_rejects_unknown_terminator():
    """Unknown line_terminator is rejected at construction."""
    with pytest.raises(ValueError, match='line_terminator'):
        RegexParser(r'(?P<sound_speed>\d+)', line_terminator='nope')


def test_regex_rejects_empty_pattern():
    """An empty pattern is rejected at construction."""
    with pytest.raises(ValueError):
        RegexParser('', line_terminator='lf')
