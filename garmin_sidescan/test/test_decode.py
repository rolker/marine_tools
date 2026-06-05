"""
Decode tests against a real GCV survey-capture fixture.

``fixtures/gcv_real_pings.bin`` holds 16 real ``eb07`` imagery payloads
extracted from a GCV-10 survey pcap (two full channel runs plus a partial),
stored as ``<u16 length><payload>`` records.  This validates the decode on
genuine bytes with no scapy/numpy runtime dependency.
"""
import os
import struct

from garmin_sidescan.decode import dark_layer, PingAssembler

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
    # GCV-10 survey data streams two SideVu channels (port=3, stbd=1)
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
