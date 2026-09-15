"""Tests for sound_speed_bridge.parsers."""

import math

import pytest

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
    trailing \n that the parser must silently skip. raw_bytes for the
    second sentence must not leak the prior terminator's \n -- the
    passthrough sink contract is that raw_bytes is exactly the framed
    sentence.
    """
    p = AMLParser()
    readings = _readings(p, b'1500.000\r\r\n1500.500\r\r\n')
    values = [r.sound_speed_m_s for r in readings]
    raw_mm_s = [r.raw_mm_s for r in readings]
    assert values == [1500.0, 1500.5]
    assert raw_mm_s == [1500000, 1500500]
    raw = [r.raw_bytes for r in readings]
    assert raw == [b'1500.000\r', b'1500.500\r']


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


# --- Accumulation-buffer cap (rolker/marine_tools#78) -----------------------
#
# The cap bounds the *unframed residue* after framing, so a serial read
# larger than the cap still frames every complete sentence it carried. When
# the residue does overflow, the oldest bytes go and the parser discards
# through the next terminator: the survivor starts mid-sentence, and framing
# it would publish a head-truncated fragment as a whole sentence.

CAP = 256  # AMLParser.MIN_MAX_BUFFER_BYTES: the smallest legal cap


def test_aml_buffer_is_capped():
    """Terminator-free bytes past the cap are bounded, and the drop is counted."""
    p = AMLParser(max_buffer_bytes=CAP)
    assert _readings(p, b'9' * 5000) == []
    assert len(p._buffer) <= CAP
    assert p.buffer_dropped_bytes == 5000 - CAP
    assert p.buffer_trim_count > 0


def test_aml_trim_discards_through_next_terminator():
    """
    The fragment left by a trim is never framed as a sentence.

    Feeding garbage past the cap and then a terminator flushes the suspect
    residue and must yield nothing at all -- publishing the truncated head
    as a sentence would put a garbage (or, for a numeric stream, a
    plausible-but-wrong) value on the reliable topics.
    """
    p = AMLParser(max_buffer_bytes=CAP)
    _readings(p, b'9' * 1000)
    assert p.buffer_trim_count > 0
    assert _readings(p, b'\r\r\n') == []


def test_aml_resyncs_to_the_next_good_sentence():
    """After a trim, every following complete sentence frames normally."""
    p = AMLParser(max_buffer_bytes=CAP)
    _readings(p, b'9' * 1000)
    readings = _readings(p, b'99\r\r\n1500.000\r\r\n1501.000\r\r\n')
    # The broken sentence (trimmed head + '99') is discarded, not framed.
    assert [r.sound_speed_m_s for r in readings] == [1500.0, 1501.0]
    assert p.buffer_trim_count == 1


def test_aml_dropped_bytes_counts_the_resync_discard_too():
    r"""
    Every byte that never becomes a reading is counted, not just the trim.

    ``buffer_dropped_bytes`` reports how much of the stream was lost, so it
    has to include the head fragment the resync throws away as well as the
    bytes trimmed off the front. 1000 bytes of garbage at a 256-byte cap
    trims 744; the following ``99\r\r\n`` flushes the 256-byte survivor
    plus its ``99`` and the CR that ends the damaged sentence (259 more).
    Counting only the trim would report 744 for 1002 lost payload bytes.
    """
    p = AMLParser(max_buffer_bytes=CAP)
    _readings(p, b'9' * 1000)
    assert p.buffer_dropped_bytes == 1000 - CAP
    assert _readings(p, b'99\r\r\n') == []
    assert p.buffer_dropped_bytes == 1003
    # The trim count still counts trims only -- one overflow, one event.
    assert p.buffer_trim_count == 1


def test_aml_trim_preserves_the_padding_boundary():
    r"""
    A trim ending mid-CRCRLF resyncs on the CR and keeps the '\n' as padding.

    The AML terminator is a single CR, so the resync consumes it and leaves
    the padding '\n' at the head of the buffer -- which the framing loop's
    lstrip must absorb rather than framing an empty sentence out of it.
    """
    p = AMLParser(max_buffer_bytes=CAP)
    _readings(p, b'9' * 1000)
    readings = _readings(p, b'\r\n1500.000\r\r\n')
    assert len(readings) == 1
    assert readings[0].sound_speed_m_s == 1500.0
    assert readings[0].raw_bytes == b'1500.000\r'


def test_aml_no_trim_when_an_oversize_chunk_frames_completely():
    """
    A read chunk far larger than the cap loses nothing if it frames.

    Regression test for trimming on append: trimming before the framing loop
    ran would silently drop complete sentences out of any chunk bigger than
    the cap (node.py reads 256 bytes at a time).
    """
    p = AMLParser(max_buffer_bytes=CAP)
    chunk = b'1500.000\r\r\n' * 100
    assert len(chunk) > CAP
    readings = _readings(p, chunk)
    assert len(readings) == 100
    assert p.buffer_trim_count == 0
    assert p.buffer_dropped_bytes == 0


def test_aml_no_trim_at_exactly_the_cap():
    """A residue of exactly max_buffer_bytes is not a trim."""
    p = AMLParser(max_buffer_bytes=CAP)
    assert _readings(p, b'9' * CAP) == []
    assert p.buffer_trim_count == 0
    assert len(p._buffer) == CAP


@pytest.mark.parametrize('bad', [0, -1, 1, 255])
def test_aml_rejects_max_buffer_bytes_below_the_floor(bad):
    """A cap below the serial read size would shred healthy traffic."""
    with pytest.raises(ValueError, match='max_buffer_bytes'):
        AMLParser(max_buffer_bytes=bad)


@pytest.mark.parametrize('bad', [4096.0, '4096', None, True])
def test_aml_rejects_non_integer_max_buffer_bytes(bad):
    """A non-integer cap is rejected at construction, not silently coerced."""
    with pytest.raises(ValueError, match='max_buffer_bytes'):
        AMLParser(max_buffer_bytes=bad)
