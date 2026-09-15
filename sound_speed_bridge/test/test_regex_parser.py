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


# --- Accumulation-buffer cap (rolker/marine_tools#78) -----------------------
#
# RegexParser is the parser the cap most matters for: its framing is exact
# (a misconfigured line_terminator never frames at all, for the whole run),
# and _parse uses re.search, so a head-truncated fragment can match a
# plausible but wrong sound speed anywhere in the line and publish it on the
# RELIABLE sound_speed topic. A wrong-but-credible value is worse than NaN.

CAP = 256  # RegexParser.MIN_MAX_BUFFER_BYTES: the smallest legal cap

_NUMBER = r'(?P<sound_speed>[0-9.]+)'


def test_regex_buffer_is_capped():
    """Bytes that never frame are bounded, and the drop is counted."""
    p = RegexParser(_NUMBER, line_terminator='lf', max_buffer_bytes=CAP)
    assert _readings(p, b'1' * 5000) == []
    assert len(p._buffer) <= CAP
    assert p.buffer_dropped_bytes == 5000 - CAP
    assert p.buffer_trim_count > 0


def test_regex_trimmed_fragment_never_publishes_a_value():
    """
    A trimmed digit run must not search-match a plausible sound speed.

    Without the discard-through-terminator rule the retained tail would
    frame as a sentence and re.search would happily pull a number out of
    it -- a wrong reading indistinguishable from a real one downstream.
    """
    p = RegexParser(_NUMBER, line_terminator='lf', max_buffer_bytes=CAP)
    _readings(p, b'1500.5' * 200)
    assert p.buffer_trim_count > 0
    assert _readings(p, b'\n') == []


def test_regex_resyncs_to_the_next_good_sentence():
    """After a trim, the following complete sentences frame normally."""
    p = RegexParser(_NUMBER, line_terminator='lf', max_buffer_bytes=CAP)
    _readings(p, b'1' * 1000)
    readings = _readings(p, b'11\n1500.5\n1501.5\n')
    assert [r.sound_speed_m_s for r in readings] == [1500.5, 1501.5]
    assert p.buffer_trim_count == 1


def test_regex_crlf_terminator_straddling_a_trim():
    r"""
    A CRLF split across the trim boundary still resyncs on that terminator.

    Drop-oldest keeps the newest bytes precisely so a retained '\r' whose
    '\n' arrives in the next chunk is still recognized as the terminator.
    Clearing the buffer outright would miss it and swallow one extra
    sentence. (A trim can never cut *inside* a complete CRLF: if both bytes
    were buffered, framing or resync would already have consumed them.)
    """
    p = RegexParser(_NUMBER, line_terminator='crlf', max_buffer_bytes=CAP)
    assert _readings(p, b'1' * CAP + b'\r') == []
    assert p.buffer_trim_count == 1
    assert p._buffer.endswith(b'\r')
    readings = _readings(p, b'\n1500.5\r\n')
    assert [r.sound_speed_m_s for r in readings] == [1500.5]


def test_regex_no_trim_when_an_oversize_chunk_frames_completely():
    """
    A read chunk far larger than the cap loses nothing if it frames.

    Regression test for trimming on append, which would have dropped whole
    sentences out of any chunk bigger than the cap.
    """
    p = RegexParser(_NUMBER, line_terminator='lf', max_buffer_bytes=CAP)
    chunk = b'1500.5\n' * 100
    assert len(chunk) > CAP
    readings = _readings(p, chunk)
    assert len(readings) == 100
    assert p.buffer_trim_count == 0
    assert p.buffer_dropped_bytes == 0


def test_regex_no_trim_at_exactly_the_cap():
    """A residue of exactly max_buffer_bytes is not a trim."""
    p = RegexParser(_NUMBER, line_terminator='lf', max_buffer_bytes=CAP)
    assert _readings(p, b'1' * CAP) == []
    assert p.buffer_trim_count == 0
    assert len(p._buffer) == CAP


@pytest.mark.parametrize('bad', [0, -1, 1, 255])
def test_regex_rejects_max_buffer_bytes_below_the_floor(bad):
    """A cap below the serial read size would shred healthy traffic."""
    with pytest.raises(ValueError, match='max_buffer_bytes'):
        RegexParser(_NUMBER, line_terminator='lf', max_buffer_bytes=bad)


@pytest.mark.parametrize('bad', [4096.0, '4096', None, True])
def test_regex_rejects_non_integer_max_buffer_bytes(bad):
    """A non-integer cap is rejected at construction, not silently coerced."""
    with pytest.raises(ValueError, match='max_buffer_bytes'):
        RegexParser(_NUMBER, line_terminator='lf', max_buffer_bytes=bad)
