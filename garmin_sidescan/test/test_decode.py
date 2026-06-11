"""
Decode tests against a real GCV survey-capture fixture.

``fixtures/gcv_real_pings.bin`` holds 16 real ``eb07`` imagery payloads
extracted from a GCV-10 survey pcap (two full channel runs plus a partial),
stored as ``<u16 length><payload>`` records.  This validates the decode on
genuine bytes with no scapy/numpy runtime dependency.
"""
import os
import struct

from garmin_sidescan.decode import (
    dark_layer, echo_layer, FH, GEN_BY_TAG, GEN_TAG_OFFSET, is_water_column,
    PingAssembler, SH, status_depth_m, status_subtype, status_transmitting,
    strip_first_layer_trailer, strip_leading_ping_header, TRAILER_MAGIC)

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'gcv_real_pings.bin')
# Real GCV-20 capture (2026-06-09 wet test, issue #26): a contiguous window of
# raw eb07/d807 payloads spanning down-look (ch2) + both side-scan (ch0/ch1)
# runs, same <u16 length><payload> record format as the GCV-10 fixture.
GCV20_FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'gcv20_real_pings.bin')


def _load_records(path):
    """Return the payloads stored in a length-prefixed fixture file."""
    with open(path, 'rb') as handle:
        data = handle.read()
    payloads = []
    i = 0
    while i + 2 <= len(data):
        (n,) = struct.unpack_from('<H', data, i)
        i += 2
        payloads.append(data[i:i + n])
        i += n
    return payloads


def load_fixture():
    """Return the list of payloads stored in the GCV-10 fixture."""
    return _load_records(FIXTURE)


def test_dark_layer_extracts_a_plausible_sample_run():
    payloads = load_fixture()
    # first record is a full eb07 packet; its dark layer is ~300 samples
    samples = dark_layer(payloads[0])
    assert 250 <= len(samples) <= 320


def test_real_capture_decodes_to_two_channel_scan_lines():
    payloads = load_fixture()
    assembler = PingAssembler()
    pings = []
    for pl in payloads:
        pings.extend(assembler.feed(pl))
    pings.extend(assembler.flush())

    channels = {ch for ch, _samples, _stamp in pings}
    # GCV-10 survey data streams two side-scan channels (port=3, stbd=1)
    assert channels == {1, 3}

    # the two complete runs (7 packets each) form ~2048-bin scan lines
    full = [len(s) for _ch, s, _t in pings if len(s) > 1000]
    assert len(full) >= 2
    for width in full:
        assert 2000 <= width <= 2100


def test_assembler_stamps_run_with_first_packet_time():
    assembler = PingAssembler()
    # two synthetic full packets of one channel, then a marker to flush.
    # Total length must exceed MIN_DATA_LEN (32); channel byte at offset 12.
    pkt = bytes([0xeb, 0x07, 0, 0]) + bytes(8) + bytes([5]) + bytes(20) \
        + bytes([174, 2, 172, 2]) + bytes([10, 20, 30])
    assembler.feed(pkt, recv_time=100.0)
    out = assembler.feed(pkt, recv_time=101.0)
    assert out == []                       # same channel, still accumulating
    out = assembler.feed(bytes([0xd8, 0x07]))   # marker flushes the run
    assert len(out) == 1
    ch, samples, stamp = out[0]
    assert ch == 5
    assert stamp == 100.0                  # first packet's time, not the second
    assert samples == bytes([10, 20, 30]) * 2


# ----- GCV-20 echo extraction (echo_layer) -------------------------------

def _gcv20_packet(channel, first_layer, tail=b''):
    # eb07 0000 + LE len + 12-byte sub-header (channel at offset 12) + FH layer
    # [+ SH + tail]. echo_layer keys off the FH/SH signatures, not fixed offsets.
    head = (bytes([0xeb, 0x07, 0, 0]) + bytes(4)
            + bytes([0x0e, 1, 3, 9, channel, 0, 0, 0]))
    return head + FH + first_layer + (SH + tail if tail else b'')


def test_echo_layer_returns_first_layer_uint16_bytes():
    # First layer returned as-is = uint16-LE samples (low byte then high byte)
    pkt = _gcv20_packet(0, bytes([0x10, 0x01, 0x20, 0x02]), tail=bytes(2))
    assert echo_layer(pkt) == bytes([0x10, 0x01, 0x20, 0x02])
    # interpreted as uint16-LE: 0x0110, 0x0220
    assert struct.unpack('<2H', echo_layer(pkt)) == (0x0110, 0x0220)


def test_echo_layer_trims_to_whole_samples():
    # odd-length first layer: drop the trailing byte so concatenation can't
    # straddle a uint16 sample across the packet boundary
    pkt = _gcv20_packet(0, bytes([10, 1, 20, 2, 99]), tail=bytes(2))
    assert echo_layer(pkt) == bytes([10, 1, 20, 2])


def test_echo_layer_runs_to_end_without_sh():
    # down-look-style: no following SH -> first layer runs to end of packet
    pkt = _gcv20_packet(2, bytes([10, 1, 20, 2, 30, 3]))
    assert echo_layer(pkt) == bytes([10, 1, 20, 2, 30, 3])


def test_echo_layer_empty_without_first_header():
    assert echo_layer(bytes([0xeb, 0x07, 0, 0]) + bytes(40)) == b''


def test_generation_tag_byte_discriminates():
    # sub-header value-width tag at offset 13: 0x11=GCV-10, 0x12=GCV-20
    g10 = _gcv20_packet(0, bytes(8))
    g20 = _gcv20_packet(0, bytes(8))
    g10 = g10[:GEN_TAG_OFFSET] + bytes([0x11]) + g10[GEN_TAG_OFFSET + 1:]
    g20 = g20[:GEN_TAG_OFFSET] + bytes([0x12]) + g20[GEN_TAG_OFFSET + 1:]
    assert GEN_BY_TAG.get(g10[GEN_TAG_OFFSET]) == 'gcv10'
    assert GEN_BY_TAG.get(g20[GEN_TAG_OFFSET]) == 'gcv20'
    assert GEN_BY_TAG.get(0x99) is None          # unknown tag -> undecided


def _img_with_layer(layer):
    # render-layer byte at offset 8 (0x0d=down-look, 0x0e/0x0f=side-scan)
    return bytes([0xeb, 0x07, 0, 0]) + bytes(4) + bytes([layer, 1, 3, 9, 0]) + bytes(20)


def test_is_water_column_by_layer_byte():
    assert is_water_column(_img_with_layer(0x0d)) is True    # down-look
    assert is_water_column(_img_with_layer(0x0e)) is False   # side-scan (GCV-20)
    assert is_water_column(_img_with_layer(0x0f)) is False   # side-scan (GCV-10)


def test_status_transmitting():
    def frame(b9):       # 8e03 status frame with byte[9] = transmit flag
        return bytes([0x8e, 0x03, 0, 0]) + bytes(5) + bytes([b9]) + bytes(24)
    assert status_transmitting(frame(0x00)) is True       # 0x00 = transmitting
    assert status_transmitting(frame(0x01)) is False      # 0x01 = off
    assert status_transmitting(bytes([0xeb, 0x07]) + bytes(40)) is None  # not status
    assert status_transmitting(bytes([0x8e, 0x03])) is None              # too short
    assert is_water_column(b'\xeb\x07') is False             # too short, safe


# ----- :50050 status sub-types + nadir depth (issue #16) -----------------
# Real 34-byte 8e03 frames from the 2026-06-10 Piscataqua capture
# (bag_2026-06-10T15.54.41_sidescan_raw), depth cross-validated vs the M3.
_STATUS_DEPTH_DEEP = bytes.fromhex(
    '8e0300001a00000002e40a0c0000030100000000d8ba0000e0a0910b010474530000')
_STATUS_DEPTH_SHOAL = bytes.fromhex(
    '8e0300001a00000002e40a0c00000301000000009c760000e0a0910b010492530000')
_STATUS_SETTINGS = bytes.fromhex(
    '8e0300001a00000002000a0c0000030100ae05c075ae05c0e0a0910b010476530000')


def test_status_subtype_discriminates():
    assert status_subtype(_STATUS_DEPTH_DEEP) == 0xe4     # depth broadcast
    assert status_subtype(_STATUS_SETTINGS) == 0x00       # settings echo
    assert status_subtype(bytes([0xeb, 0x07]) + bytes(40)) is None   # not status
    assert status_subtype(bytes([0x8e, 0x03])) is None               # too short


def test_status_depth_decodes_feet_milli_to_metres():
    # u16 LE @ offset 20 in feet*1000: 47832 -> 47.832 ft -> 14.579 m
    assert abs(status_depth_m(_STATUS_DEPTH_DEEP) - 14.579) < 0.01
    # shoal (Cod Rock pass): 30364 -> 30.364 ft -> 9.255 m
    assert abs(status_depth_m(_STATUS_DEPTH_SHOAL) - 9.255) < 0.01


def test_status_depth_none_for_non_depth_frames():
    assert status_depth_m(_STATUS_SETTINGS) is None        # settings sub-type
    assert status_depth_m(bytes([0xeb, 0x07]) + bytes(40)) is None   # not status
    assert status_depth_m(bytes([0x8e, 0x03, 0, 0]) + bytes(5)
                          + bytes([0xe4])) is None         # 0xe4 but too short


def test_status_transmitting_ignores_depth_subtype():
    # The depth frame is broadcast regardless of transmit state, so it must not
    # flap the transmit flag to "off" (it shares byte 9 with the tx flag).
    assert status_transmitting(_STATUS_DEPTH_DEEP) is None
    assert status_transmitting(_STATUS_SETTINGS) is True


# ----- GCV-20 trailer / leading-header stripping (issue #26) --------------

def test_strip_first_layer_trailer_removes_delimited_trailer():
    # samples (no trailer magic) followed by a real trailer record
    samples = bytes(range(40))
    trailer = bytes([0x43, 0x81, 0x96, 0x03, 0x49, 0x00,
                     0x52, 0x80, 0x10, 0x5a, 0xc6, 0x70])
    assert strip_first_layer_trailer(samples + trailer) == samples


def test_strip_first_layer_trailer_keeps_clean_layer():
    # no trailer present -> unchanged (the delimiter, not a fixed length, bounds it)
    samples = bytes(range(40))
    assert strip_first_layer_trailer(samples) == samples


def test_strip_first_layer_trailer_ignores_magic_far_from_end():
    # a 52 80 10 byte sequence deep in the samples must not trigger a cut
    layer = TRAILER_MAGIC + bytes(60)
    assert strip_first_layer_trailer(layer) == layer


def test_strip_leading_ping_header_drops_long_run():
    block = b'\xb2\xad' * 19 + bytes([0x4f, 0x39, 0x8b, 0x37])
    assert strip_leading_ping_header(block) == bytes([0x4f, 0x39, 0x8b, 0x37])


def test_strip_leading_ping_header_keeps_short_run():
    # a short repeat in genuine samples is left intact
    block = b'\xb2\xad' * 3 + bytes([1, 2, 3, 4])
    assert strip_leading_ping_header(block) == block


def test_gcv20_real_capture_strips_trailer_and_leading_band():
    # The without-fix concatenation leaks 7 trailer records (one per packet) and
    # a long constant leading run into every scan line -> the bright lines.
    payloads = _load_records(GCV20_FIXTURE)
    assembler = PingAssembler(echo_layer)
    pings = []
    for pl in payloads:
        pings.extend(assembler.feed(pl))
    pings.extend(assembler.flush())

    # fixture spans down-look (ch2) + both side-scan (ch0/ch1) runs
    assert {ch for ch, _s, _t in pings} >= {0, 1, 2}
    assert len(pings) >= 3
    for ch, samples, _t in pings:
        # no per-packet trailer bytes survive into the scan line
        assert TRAILER_MAGIC not in samples, f'trailer leaked into ch{ch}'
        # no long constant leading run (the bright near-range band)
        pat = samples[:2]
        run = 0
        while run + 2 <= len(samples) and samples[run:run + 2] == pat:
            run += 2
        assert run < 24, f'leading band not stripped on ch{ch}'
        # plausible ~2048-bin scan line, whole uint16 samples
        assert len(samples) % 2 == 0
        assert 1800 <= len(samples) // 2 <= 2100


def test_assembler_uses_supplied_extractor():
    # PingAssembler routes per-packet extraction through the supplied callable.
    # First layer is 8 bytes so the packet exceeds MIN_DATA_LEN (32).
    pkt = _gcv20_packet(7, bytes([10, 1, 20, 2, 30, 3, 40, 4]), tail=bytes(2))
    assembler = PingAssembler(echo_layer)
    assert assembler.feed(pkt) == []                # accumulating
    out = assembler.feed(bytes([0xd8, 0x07]))       # marker flushes
    assert len(out) == 1
    ch, samples, _stamp = out[0]
    assert ch == 7
    assert samples == bytes([10, 1, 20, 2, 30, 3, 40, 4])
