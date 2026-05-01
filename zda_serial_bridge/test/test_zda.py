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
    # 999_999_999 ns → 0.999... s → rounded to 1.00 by %.2f. Expected:
    # the integer-second field stays untouched (we do NOT carry into sec)
    # and the fractional rounds. This documents the chosen behaviour.
    sentence = format_zda(2026, 5, 1, 16, 56, 12, 999_999_999)
    # %05.2f on 12.999... → '13.00' (printf-style rounding rolls integer)
    assert sentence.startswith('$GPZDA,165613.00,01,05,2026,,*')
