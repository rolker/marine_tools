"""
Unit tests for the GCV TCP command frame builders.

The expected byte strings are Dan Tauriello's known-good frames, verified live
on both the GCV-10 and GCV-20 (2026-06-05).
"""
from garmin_sidescan.commands import (
    build_interference_cmd,
    build_range_cmd,
    build_tvg_cmd,
    encode_leb128,
    TRANSMIT_OFF,
    TRANSMIT_ON,
)


def test_transmit_frames_are_five_channels_with_correct_flag():
    assert len(TRANSMIT_ON) == 5 * (8 + 10)
    assert len(TRANSMIT_OFF) == 5 * (8 + 10)
    # transmit-on frames end each 10-byte payload with a9 01 00, off with a9 01 01
    assert TRANSMIT_ON.count(bytes.fromhex('a90100')) == 5
    assert TRANSMIT_OFF.count(bytes.fromhex('a90101')) == 5


def test_leb128_matches_known_range_values():
    # 12 m = 24000 half-mm units, 50 m = 100000 half-mm units
    assert encode_leb128(24000) == bytes.fromhex('c0bb01')
    assert encode_leb128(100000) == bytes.fromhex('a08d06')
    assert encode_leb128(0) == b'\x00'


def test_build_range_cmd_reproduces_known_frames():
    exp12 = bytes.fromhex(
        'd207efbe0b000000010708010201015bc0bb01'
        'd207efbe0b000000010708010201025bc0bb01'
        'd207efbe0b000000010708010201035bc0bb01')
    exp50 = bytes.fromhex(
        'd207efbe0b000000010708010201015ba08d06'
        'd207efbe0b000000010708010201025ba08d06'
        'd207efbe0b000000010708010201035ba08d06')
    assert build_range_cmd(12) == exp12
    assert build_range_cmd(50) == exp50


def test_tvg_frames_match_known_good():
    # Dan's verified TVG frames: opcode 89, level 0..3
    assert build_tvg_cmd(0) == bytes.fromhex('d207efbe0a00000001070701020100890100')
    assert build_tvg_cmd(1) == bytes.fromhex('d207efbe0a00000001070701020100890101')
    assert build_tvg_cmd(2) == bytes.fromhex('d207efbe0a00000001070701020100890102')
    assert build_tvg_cmd(3) == bytes.fromhex('d207efbe0a00000001070701020100890103')


def test_interference_frames_use_a1_opcode():
    # off/med/high match Dan's frames; low is rebuilt (his interference_low.py
    # held the TVG_low bytes by mistake) as the consistent a1 01 01.
    assert build_interference_cmd(0) == bytes.fromhex('d207efbe0a00000001070701020100a10100')
    assert build_interference_cmd(1) == bytes.fromhex('d207efbe0a00000001070701020100a10101')
    assert build_interference_cmd(2) == bytes.fromhex('d207efbe0a00000001070701020100a10102')
    assert build_interference_cmd(3) == bytes.fromhex('d207efbe0a00000001070701020100a10103')
