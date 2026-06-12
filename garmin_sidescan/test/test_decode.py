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
    dark_layer, decode_leb128, derive_sample_rate, echo_layer, FH, GEN_BY_TAG,
    GEN_TAG_OFFSET, generation_from_layers, is_water_column,
    parse_downlook_subheader, parse_subheader, PingAssembler, SH,
    status_subtype, status_transmitting, strip_first_layer_trailer,
    strip_leading_ping_header, Subheader, subheader_bottom_range_m,
    TRAILER_MAGIC)

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

    channels = {p.channel for p in pings}
    # GCV-10 survey data streams two side-scan channels (port=3, stbd=1)
    assert channels == {1, 3}

    # the two complete runs (7 packets each) form ~2048-bin scan lines
    full = [len(p.samples) for p in pings if len(p.samples) > 1000]
    assert len(full) >= 2
    for width in full:
        assert 2000 <= width <= 2100

    # The GCV-10 sub-header does not match the GCV-20 varint layout, so the
    # assembler attaches no sub-header -- the driver's fallback chain (the
    # commanded-range mirror) is load-bearing for this generation.
    assert all(p.subheader is None for p in pings)


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
    line = out[0]
    assert line.channel == 5
    assert line.stamp == 100.0             # first packet's time, not the second
    assert line.samples == bytes([10, 20, 30]) * 2
    assert line.subheader is None          # synthetic packet: no parseable sub


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


def test_generation_from_layers_on_real_fixtures():
    # Range-INDEPENDENT generation: GCV-10 packets carry >=2 SH/SHS later-layer
    # headers (3 layers); GCV-20 carry <=1 (<=2 layers). Validated on the real
    # capture fixtures (the byte-13 'gen tag' is actually the range bracket).
    g10 = [generation_from_layers(p) for p in load_fixture() if p[:2] == b'\xeb\x07']
    g20 = [generation_from_layers(p) for p in _load_records(GCV20_FIXTURE)
           if p[:2] == b'\xeb\x07']
    assert any(g10) and all(g == 'gcv10' for g in g10 if g)   # GCV-10 capture
    assert any(g20) and all(g == 'gcv20' for g in g20 if g)   # GCV-20 capture


def test_generation_from_layers_synthetic():
    pre = bytes([0xeb, 0x07, 0, 0]) + bytes(4) + bytes([0x0e, 1, 3, 9, 0, 0, 0, 0])
    assert generation_from_layers(pre + FH + bytes(20)) == 'gcv20'                  # 0 SH
    assert generation_from_layers(pre + FH + bytes(20) + SH + bytes(20)) == 'gcv20'  # 1 SH
    assert generation_from_layers(pre + FH + SH + bytes(8) + SH + bytes(8)) == 'gcv10'  # 2 SH
    assert generation_from_layers(bytes([0xeb, 0x07, 0, 0]) + bytes(40)) is None    # no FH
    assert generation_from_layers(bytes([0xd8, 0x07]) + bytes(40)) is None          # not eb07


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


# ----- :50050 status sub-types (issue #16) -------------------------------
# Real 34-byte 8e03 frames from the 2026-06-10 Piscataqua capture
# (bag_2026-06-10T15.54.41_sidescan_raw). The 0xe4 sub-type holds a u16 @ offset
# 20 we once took for nadir depth, but the M3 cross-check showed it is held and
# does NOT track depth, so the driver no longer decodes it -- byte 9 is kept only
# as a sub-type/mode selector that the transmit-flag read must not trip over.
_STATUS_SUBTYPE_E4 = bytes.fromhex(
    '8e0300001a00000002e40a0c0000030100000000d8ba0000e0a0910b010474530000')
_STATUS_SETTINGS = bytes.fromhex(
    '8e0300001a00000002000a0c0000030100ae05c075ae05c0e0a0910b010476530000')


def test_status_subtype_discriminates():
    assert status_subtype(_STATUS_SUBTYPE_E4) == 0xe4     # mode/status sub-type
    assert status_subtype(_STATUS_SETTINGS) == 0x00       # settings echo
    assert status_subtype(bytes([0xeb, 0x07]) + bytes(40)) is None   # not status
    assert status_subtype(bytes([0x8e, 0x03])) is None               # too short


def test_status_transmitting_ignores_e4_subtype():
    # The 0xe4 sub-type is broadcast regardless of transmit state, so it must not
    # flap the transmit flag to "off" (it shares byte 9 with the tx flag).
    assert status_transmitting(_STATUS_SUBTYPE_E4) is None
    assert status_transmitting(_STATUS_SETTINGS) is True


# ----- down-look bottom-range varint (sub-header offset 14) ----------------

def test_decode_leb128():
    assert decode_leb128(bytes([0x05]), 0) == (5, 1)
    assert decode_leb128(bytes([0x90, 0x7f]), 0) == (16272, 2)   # real value
    assert decode_leb128(bytes([0x80]), 0) == (None, 1)          # unterminated


def test_subheader_bottom_range_m():
    # Real GCV-20 down-look sub-header (offset 8 = 0x0d layer, 13 = bracket,
    # 14 = LEB128 bottom-range varint). 90 7f = 16272 * 0.5 mm = 8.136 m.
    down = bytes.fromhex('eb07000000000000') + bytes.fromhex('0d0103090212907f190023')
    assert abs(subheader_bottom_range_m(down) - 8.136) < 0.01
    # side-scan layer (0x0e) -> None (no bottom range)
    side = bytes.fromhex('eb07000000000000') + bytes.fromhex('0e0103090212907f190023')
    assert subheader_bottom_range_m(side) is None
    assert subheader_bottom_range_m(bytes([0xd8, 0x07]) + bytes(20)) is None  # not eb07


def test_parse_downlook_subheader():
    # Real ~7.9 m down-look sub-header: v1=16272 (8.136 m), 19 00 23, v2=24236
    # (12.118 m), 2a, v3=178 (0.089 m), 31 02 3f, FH.
    pkt = bytes.fromhex('eb07000000000000') + bytes.fromhex(
        '0d0103090212907f190023acbd012ab20131023fda04d804') + bytes(8)  # + samples
    sub = parse_downlook_subheader(pkt)
    assert sub.bracket == 0x12
    assert abs(sub.bottom_range_m - 8.136) < 0.01     # v1
    assert abs(sub.display_range_m - 12.118) < 0.01   # v2
    assert abs(sub.near_field_m - 0.089) < 0.01       # v3
    # garbled marker / non-down-look -> None (markers are validated)
    assert parse_downlook_subheader(bytes.fromhex('eb07000000000000') + bytes(30)) is None


def test_parse_subheader_side_scan_has_own_display_range():
    # Real side-scan (port) sub-header: layer 0x0e, v1 shared (39378 = 19.69 m),
    # but v2 = its own across-track range (100798 = 50.40 m, ~2x the water column).
    port = bytes.fromhex('eb07000000000000') + bytes.fromhex(
        '0e0103090013d2b302190023be93062ae50131023fda04d80432')
    sub = parse_subheader(port)
    assert sub.channel == 0 and sub.layer == 0x0e
    assert abs(sub.bottom_range_m - 19.689) < 0.01      # v1 (shared bottom depth)
    assert abs(sub.display_range_m - 50.399) < 0.01     # v2 (across-track range)
    # the down-look-only wrapper rejects a side-scan packet
    assert parse_downlook_subheader(port) is None


# ----- per-run sub-header attachment + scale derivation (issue #35) --------

def test_assembler_attaches_run_subheader_on_gcv20_capture():
    # Every scan line assembled from the real GCV-20 capture must carry its
    # own run's sub-header: matching channel, plausible v1/v2 ranges. This is
    # what the driver scales sample_rate (v2) and the nadir depth (v1) from.
    assembler = PingAssembler(echo_layer)
    pings = []
    for pl in _load_records(GCV20_FIXTURE):
        pings.extend(assembler.feed(pl))
    pings.extend(assembler.flush())
    assert pings
    for p in pings:
        assert p.subheader is not None
        assert p.subheader.channel == p.channel
        assert p.subheader.bottom_range_m > 0
        assert p.subheader.display_range_m > 0


def _sub(v2, v1=10.0):
    return Subheader(channel=2, layer=0x0d, bracket=0x12,
                     bottom_range_m=v1, display_range_m=v2, near_field_m=0.1)


def test_derive_sample_rate_uses_own_channel_v2():
    # A consumer recovers range = sv*bins/(2*rate); each channel's own v2 must
    # round-trip, so the down-look (water column) and side-scan (slant swath)
    # get DIFFERENT rates even with identical bins -- the asymmetry that a
    # single commanded range cannot express.
    sv, bins = 1500.0, 2048
    down = derive_sample_rate(_sub(21.0), bins, sv, commanded_range_m=50.0)
    side = derive_sample_rate(_sub(50.4), bins, sv, commanded_range_m=50.0)
    assert abs(sv * bins / (2.0 * down) - 21.0) < 1e-9
    assert abs(sv * bins / (2.0 * side) - 50.4) < 1e-9
    assert down != side


def test_derive_sample_rate_fallback_chain():
    sv, bins = 1500.0, 2048
    # no sub-header (e.g. GCV-10) -> the commanded range
    rate = derive_sample_rate(None, bins, sv, commanded_range_m=50.0)
    assert abs(sv * bins / (2.0 * rate) - 50.0) < 1e-9
    # no sub-header, nothing commanded -> the configured fallback rate
    assert derive_sample_rate(None, bins, sv, 0.0, fallback_rate=7.5) == 7.5
    # ...which defaults to 0.0 = "unavailable" (RawSonarImage convention)
    assert derive_sample_rate(None, bins, sv, 0.0) == 0.0
    # a range without a sound speed (or bins) cannot derive a rate
    assert derive_sample_rate(_sub(21.0), bins, 0.0, 50.0, fallback_rate=7.5) == 7.5
    assert derive_sample_rate(_sub(21.0), 0, sv, 50.0, fallback_rate=7.5) == 7.5


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


def test_strip_first_layer_trailer_handles_firmware_without_96_03_opener():
    # The 2026-06-10 GCV-20 wet capture trailer has an 'e6 24' opener, not the
    # bench capture's '96 03'. Keying on '96 03' alone left this trailer in,
    # which read back as constant per-packet bands in the waterfall. Anchor on
    # the 0x43 opener + 52 80 10 magic instead. (Real bytes, both channels.)
    samples = bytes(range(40))
    down = bytes.fromhex('43d1e62449005280105b9ead026baaa40c')   # down-look
    side = bytes.fromhex('43d2e6244aac025280105bc2af0267')       # side-scan
    assert strip_first_layer_trailer(samples + down) == samples
    assert strip_first_layer_trailer(samples + side) == samples


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
    assert {p.channel for p in pings} >= {0, 1, 2}
    assert len(pings) >= 3
    for ch, samples, _t, _sub in pings:
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
    assert out[0].channel == 7
    assert out[0].samples == bytes([10, 1, 20, 2, 30, 3, 40, 4])


def test_d807_marker_tags_an_adjacent_run_channel():
    # Markers bracket channel runs in close/open pairs; each tags an adjacent
    # run's channel in its trailing `19 <channel>` bytes (gcv_protocol.md
    # section 3.C -- 94% verified with bracket+v1 on the 2026-06-11 capture).
    # Pin the structure on the real GCV-20 fixture window.
    records = _load_records(GCV20_FIXTURE)
    run_ch = [pl[12] if pl[:2] == b'\xeb\x07' and len(pl) > 32 else None
              for pl in records]
    checked = 0
    for i, pl in enumerate(records):
        if pl[:2] != b'\xd8\x07' or len(pl) < 10:
            continue
        assert pl[8] == 0x02                  # constant record opener
        assert pl[-2] == 0x19                 # channel tag marker
        prev_ch = next((c for c in reversed(run_ch[:i]) if c is not None), None)
        next_ch = next((c for c in run_ch[i + 1:] if c is not None), None)
        assert pl[-1] in {prev_ch, next_ch} - {None}   # tags an adjacent run
        checked += 1
    assert checked >= 2                       # fixture spans multiple runs
