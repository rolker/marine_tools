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
    PingAssembler, SH)

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'gcv_real_pings.bin')


def load_fixture():
    """Return the list of payloads stored in the length-prefixed fixture."""
    with open(FIXTURE, 'rb') as handle:
        data = handle.read()
    payloads = []
    i = 0
    while i + 2 <= len(data):
        (n,) = struct.unpack_from('<H', data, i)
        i += 2
        payloads.append(data[i:i + n])
        i += n
    return payloads


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
    assert is_water_column(b'\xeb\x07') is False             # too short, safe


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
