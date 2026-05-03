"""Unit tests for the ZDA NMEA sentence formatter."""

from zda_serial_bridge.node import format_zda


def _split_body_and_checksum(sentence: str):
    assert sentence.startswith('$')
    assert sentence.endswith('\r\n')
    body, _, rest = sentence[1:-2].partition('*')
    return body, rest


def _xor_checksum(body: str) -> int:
    chk = 0
    for c in body:
        chk ^= ord(c)
    return chk


def test_zda_basic_format():
    sentence = format_zda(2026, 5, 1, 16, 56, 12, 500_000_000)
    assert sentence.startswith('$GPZDA,165612.50,01,05,2026,,*')
    assert sentence.endswith('\r\n')


def test_zda_checksum_matches_body():
    sentence = format_zda(2026, 5, 1, 16, 56, 12, 500_000_000)
    body, chk_str = _split_body_and_checksum(sentence)
    assert int(chk_str, 16) == _xor_checksum(body)


def test_zda_zero_padding():
    sentence = format_zda(2026, 1, 5, 0, 1, 2, 0)
    assert sentence.startswith('$GPZDA,000102.00,05,01,2026,,*')


def test_zda_leap_second():
    # Per SBG msg, sec field can be 60 during a leap second; serializer
    # must keep two integer digits (NMEA spec also permits this).
    sentence = format_zda(2026, 12, 31, 23, 59, 60, 0)
    assert sentence.startswith('$GPZDA,235960.00,31,12,2026,,*')


def test_zda_talker_id_override():
    sentence = format_zda(2026, 5, 1, 16, 56, 12, 0, talker_id='IN')
    assert sentence.startswith('$INZDA,165612.00,01,05,2026,,*')
    body, chk_str = _split_body_and_checksum(sentence)
    assert int(chk_str, 16) == _xor_checksum(body)


def test_zda_fractional_truncation_to_centiseconds():
    # 999_999_999 ns truncates to 99 cs; the integer second stays at 12.
    sentence = format_zda(2026, 5, 1, 16, 56, 12, 999_999_999)
    assert sentence.startswith('$GPZDA,165612.99,01,05,2026,,*')


def test_zda_does_not_round_across_minute_boundary():
    # sec=59 + 999_999_999 ns must NOT roll the integer second to 60 (which
    # would emit an invalid ``165960.00`` since higher fields aren't carried).
    sentence = format_zda(2026, 5, 1, 16, 59, 59, 999_999_999)
    assert sentence.startswith('$GPZDA,165959.99,01,05,2026,,*')


def test_zda_truncation_at_99_centiseconds():
    # Truncation maps the top-of-second band (>= 990 ms) to 99 cs.
    sentence = format_zda(2026, 5, 1, 0, 0, 0, 990_000_000)
    assert sentence.startswith('$GPZDA,000000.99,01,05,2026,,*')
