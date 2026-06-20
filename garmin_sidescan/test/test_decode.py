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
    dark_layer, decode_leb128, derive_sample_rate, echo_layer, FH, FHS,
    generation_from_layers, is_run_delimiter, is_water_column,
    marker_temperature_c, parse_downlook_subheader,
    parse_subheader, PingAssembler, SH, SHS, status_subtype, status_transmitting,
    strip_leading_ping_header, Subheader, subheader_bottom_range_m)

# The per-packet records the driver once mistook for an appended "trailer"
# (issue #26) include this byte sequence as field10's value; tests assert it
# never leaks into the extracted samples.
TRAILER_MAGIC = b'\x52\x80\x10'


def _d807_telemetry(temp_c, channel):
    """Build a 16-byte d807 telemetry frame: 02 0c <f32 LE> 19 <ch>."""
    return (bytes([0xd8, 0x07, 0, 0]) + struct.pack('<I', 8)
            + b'\x02\x0c' + struct.pack('<f', temp_c) + b'\x19' + bytes([channel]))


def _d807_delimiter(v1, channel):
    """Build a d807 run delimiter: 02 <0x10|len(v1)> <v1 varint> 19 <ch>."""
    body = b'\x02' + bytes([0x10 | len(v1)]) + v1 + b'\x19' + bytes([channel])
    return bytes([0xd8, 0x07, 0, 0]) + struct.pack('<I', len(body)) + body


FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'gcv_real_pings.bin')
# Real GCV-20 capture (2026-06-09 wet test, issue #26): a contiguous window of
# raw eb07/d807 payloads spanning down-look (ch2) + both side-scan (ch0/ch1)
# runs, same <u16 length><payload> record format as the GCV-10 fixture.
GCV20_FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'gcv20_real_pings.bin')
# Real GCV-10 *water-column* (down-look, 0x0d) eb07 payloads (2026-06-05 bench).
# Unlike the GCV-10 side-scan (3-layer 8-bit "dark"), the water-column is a
# single 16-bit echo layer -- so the layer-count heuristic mislabels it gcv20
# and the old device-wide dark_layer extractor blanked it (issue #60).
GCV10_WC_FIXTURE = os.path.join(
    os.path.dirname(__file__), 'fixtures', 'gcv10_watercolumn_pings.bin')


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

    # The GCV-10 sub-header is the same tagged-record grammar (its v3 is a
    # 1-byte varint, tag 0x29) -- Dan's survey ran at a commanded 20 m range,
    # so every run's own v2 must read exactly 20.00 m, with a real bottom
    # range in v1 and the channel matching the run.
    for p in pings:
        assert p.subheader is not None
        assert p.subheader.channel == p.channel
        assert abs(p.subheader.display_range_m - 20.0) < 0.01
        assert 7.0 < p.subheader.bottom_range_m < 11.0


def test_gcv10_water_column_decodes_as_16bit_echo_not_blank_dark():
    # Issue #60: the GCV-10 water-column is a single 16-bit echo layer, NOT the
    # 3-layer 8-bit "dark" side-scan form. Pin the failure modes the fix guards:
    payloads = _load_records(GCV10_WC_FIXTURE)
    assert payloads
    p = payloads[0]
    assert is_water_column(p)                       # 0x0d down-look beam
    # The layer-count heuristic mislabels the single-layer WC as 'gcv20'...
    assert generation_from_layers(p) == 'gcv20'
    # ...so a device-wide dark_layer extractor blanks it...
    assert dark_layer(p) == b''
    # ...while echo_layer recovers a real 16-bit sample run.
    assert len(echo_layer(p)) > 200

    # The assembler self-selects per packet: water-column -> echo (16-bit),
    # regardless of the device's latched generation.
    assembler = PingAssembler()
    pings = []
    for pl in payloads:
        pings.extend(assembler.feed(pl))
    pings.extend(assembler.flush())
    assert pings, 'water-column run produced no scan line (regression: blanked)'
    line = pings[-1]
    assert line.bits == 16
    assert len(line.samples) > 200


def test_gcv10_side_scan_self_selects_8bit_dark():
    # The same self-selecting assembler must still decode the GCV-10 side-scan
    # (3-layer dark form) as 8-bit -- the per-packet structural signal.
    payloads = load_fixture()
    assembler = PingAssembler()
    pings = []
    for pl in payloads:
        pings.extend(assembler.feed(pl))
    pings.extend(assembler.flush())
    full = [p for p in pings if len(p.samples) > 1000]
    assert full
    assert all(p.bits == 8 for p in full)


def test_assembler_stamps_run_with_first_packet_time():
    # Pin dark_layer: these synthetic packets carry an SH header but no
    # FH/FHS, so per-packet self-select can't classify them -- the assembly
    # mechanics under test are extractor-independent.
    assembler = PingAssembler(dark_layer)
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

def _leb128(value):
    """Encode an unsigned int as a little-endian base-128 varint."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _eb07_with_layer(channel, samples, trailing=b'', beam=0x0e):
    # Grammar-valid eb07: envelope + sub-header opener (01 03 09 <ch>) + a
    # length-delimited render-layer field (tag 0x3f = field7, L=7) whose value is
    # <LEB128 sample-byte count><samples>, then any trailing records. The payload
    # is padded to exceed MIN_DATA_LEN; the envelope length stays consistent.
    samples = bytes(samples)
    value = _leb128(len(samples)) + samples
    layer = bytes([0x3f]) + _leb128(len(value)) + value
    body = bytes([beam, 0x01, 0x03, 0x09, channel]) + layer + bytes(trailing)
    if len(body) < 26:
        body += bytes(26 - len(body))
    return bytes([0xeb, 0x07, 0, 0]) + struct.pack('<I', len(body)) + body


def test_echo_layer_returns_first_layer_uint16_bytes():
    # First render layer returned as-is = uint16-LE samples (low byte then high)
    pkt = _eb07_with_layer(0, bytes([0x10, 0x01, 0x20, 0x02]))
    assert echo_layer(pkt) == bytes([0x10, 0x01, 0x20, 0x02])
    # interpreted as uint16-LE: 0x0110, 0x0220
    assert struct.unpack('<2H', echo_layer(pkt)) == (0x0110, 0x0220)


def test_echo_layer_trims_to_whole_samples():
    # odd sample-byte count: drop the trailing byte so concatenation can't
    # straddle a uint16 sample across the packet boundary
    pkt = _eb07_with_layer(0, bytes([10, 1, 20, 2, 99]))
    assert echo_layer(pkt) == bytes([10, 1, 20, 2])


def test_echo_layer_reads_declared_length_not_packet_tail():
    # The sample region is bounded by the layer field's own LEB128 length, so the
    # records that follow it -- here a record counter whose value contains 0x43,
    # the exact issue-#26 trap that the old rfind(0x43) trailer search mis-cut --
    # never leak into the samples.
    counter_record = bytes([0x43, 0x91, 0xb5, 0x43,   # field8: counter (has 0x43)
                            0x4a, 0xac, 0x02,         # field9: offset
                            0x52, 0x80, 0x10,         # field10: 80 10
                            0x5a, 0xca, 0x5b])        # field11: range echo
    pkt = _eb07_with_layer(2, bytes([10, 1, 20, 2, 30, 3]), trailing=counter_record)
    assert echo_layer(pkt) == bytes([10, 1, 20, 2, 30, 3])


def test_echo_layer_empty_without_render_layer():
    # no sub-header opener (01 03 09) -> not an imagery record -> b''
    assert echo_layer(bytes([0xeb, 0x07, 0, 0]) + bytes(40)) == b''


def test_generation_from_layers_on_real_fixtures():
    # Range-INDEPENDENT generation: GCV-10 packets carry >=2 SH/SHS later-layer
    # headers (3 layers); GCV-20 carry <=1 (<=2 layers). Validated on the real
    # capture fixtures (byte 13 is field2's tag -- v1 varint length -- which
    # once masqueraded as a 'gen tag' and then as a 'range bracket').
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
    assert sub.v1_tag == 0x12   # field2, 2-byte v1
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
    return Subheader(channel=2, layer=0x0d, v1_tag=0x12,
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
    # no sub-header (garbled packet) -> the commanded range
    rate = derive_sample_rate(None, bins, sv, commanded_range_m=50.0)
    assert abs(sv * bins / (2.0 * rate) - 50.0) < 1e-9
    # no sub-header, nothing commanded -> the configured fallback rate
    assert derive_sample_rate(None, bins, sv, 0.0, fallback_rate=7.5) == 7.5
    # ...which defaults to 0.0 = "unavailable" (RawSonarImage convention)
    assert derive_sample_rate(None, bins, sv, 0.0) == 0.0
    # a range without a sound speed (or bins) cannot derive a rate
    assert derive_sample_rate(_sub(21.0), bins, 0.0, 50.0, fallback_rate=7.5) == 7.5
    assert derive_sample_rate(_sub(21.0), 0, sv, 50.0, fallback_rate=7.5) == 7.5


# ----- GCV-20 render-layer extraction by grammar length (issue #26) --------
# Real 2026-06-15 Lake Massabesic packets whose record-counter (field8) high byte
# is 0x43 -- the condition under which the pre-fix rfind(0x43) trailer search
# locked onto the counter byte instead of the true opener and left a bright
# 0x__43 residual sample at every packet boundary (the reported waterfall
# artifact). See ~/data/logs/analysis/2026-06-15_sidescan_artifact/.
COUNTER43_FIXTURE = os.path.join(
    os.path.dirname(__file__), 'fixtures', 'gcv20_counter43_pings.bin')


def _render_layers(payload):
    """
    Return each render layer's sample bytes by walking the eb07 grammar.

    Independently mirrors the protocol (value after the inner LEB128 count), not
    echo_layer's code, so equality with echo_layer is a real cross-check.
    """
    i, n, layers = 9, len(payload), []          # 9 = RECORD_START (after beam byte)
    while i < n:
        length_class = payload[i] & 0x07
        i += 1
        if length_class <= 6:
            i += length_class
            continue
        field_len, i = decode_leb128(payload, i)
        value = payload[i:i + field_len]
        count, off = decode_leb128(value, 0)
        layers.append(value[off:off + count])
        i += field_len
    return layers


def _old_rfind_strip(payload):
    """
    Reproduce the pre-fix extraction to witness the issue-#26 regression.

    Finds FH/FHS, then rfind(52 80 10) and rfind(0x43) for the trailer opener.
    On counter-high-byte-0x43 packets it leaves a 0x__43 residual sample that the
    grammar-length extraction never does.
    """
    f = payload.find(FH, 12)
    if f < 0:
        f = payload.find(FHS, 12)
    if f < 0:
        return b''
    ends = [p for p in (payload.find(SH, f + 4), payload.find(SHS, f + 4)) if p >= 0]
    layer = payload[f + 4:min(ends) if ends else len(payload)]
    m = layer.rfind(TRAILER_MAGIC)
    if 0 <= m and m >= len(layer) - 24:
        op = layer.rfind(0x43, max(0, m - 10), m)
        if op >= 0:
            layer = layer[:op]
    return layer[:len(layer) // 2 * 2]


def test_eb07_payload_is_a_clean_tagged_record_stream():
    # The render layers are length-delimited (L=7) fields: walking the grammar
    # from RECORD_START consumes a real packet EXACTLY to the envelope end (no
    # leftover bytes), so the sample length is read, never searched for.
    pl = next(p for p in _load_records(GCV20_FIXTURE)
              if p[:2] == b'\xeb\x07' and len(p) > 32)
    env_len = struct.unpack_from('<I', pl, 4)[0]
    i, n, n_layers = 9, len(pl), 0
    while i < n:
        length_class = pl[i] & 0x07
        i += 1
        if length_class <= 6:
            i += length_class
            continue
        field_len, i = decode_leb128(pl, i)
        i += field_len
        n_layers += 1
    assert i == n == env_len + 8           # consumed exactly to the envelope end
    layers = _render_layers(pl)
    assert layers and n_layers == len(layers)
    # echo_layer returns the first render layer, trimmed to whole uint16 samples
    first = layers[0]
    assert echo_layer(pl) == first[:len(first) // 2 * 2]


def test_echo_layer_no_0x43_residual_on_real_capture():
    payloads = _load_records(COUNTER43_FIXTURE)
    eb07 = [p for p in payloads if p[:2] == b'\xeb\x07' and len(p) > 32]
    assert eb07, 'fixture has no imagery packets'
    old_with_residual = 0
    for pl in eb07:
        first = _render_layers(pl)[0]
        # the fix returns exactly the grammar-declared samples...
        assert echo_layer(pl) == first[:len(first) // 2 * 2]
        assert TRAILER_MAGIC not in echo_layer(pl)
        # ...whereas the pre-fix rfind(0x43) search ends these packets in the
        # 0x__43 boundary residual (low byte 0x43, high byte = a counter byte)
        old = _old_rfind_strip(pl)
        if len(old) >= 2 and old[-2] == 0x43 and old[-1] >= 0x40:
            old_with_residual += 1
    assert old_with_residual == len(eb07)   # every packet tripped the old bug


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
    for ch, samples, _t, _sub, _bits in pings:
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
    pkt = _eb07_with_layer(7, bytes([10, 1, 20, 2, 30, 3, 40, 4]))
    assembler = PingAssembler(echo_layer)
    assert assembler.feed(pkt) == []                # accumulating
    out = assembler.feed(bytes([0xd8, 0x07]))       # marker flushes
    assert len(out) == 1
    assert out[0].channel == 7
    assert out[0].samples == bytes([10, 1, 20, 2, 30, 3, 40, 4])


def test_subheader_grammar_tags_encode_varint_length_on_both_fixtures():
    # The sub-header is a tagged record: tag = (field# << 3) | L where L is
    # the value's LEB128 byte length -- verified with zero exceptions on 697k+
    # frames across five captures and both generations (gcv_protocol.md
    # section 3). Pin the invariant on every fixture packet.
    from garmin_sidescan.decode import V1_TAG_OFFSET

    checked = 0
    for fixture in (FIXTURE, GCV20_FIXTURE):
        for pl in _load_records(fixture):
            if pl[:2] != b'\xeb\x07' or len(pl) <= 32:
                continue
            i = V1_TAG_OFFSET
            for want_field in (2, 3, 4, 5):
                tag = pl[i]
                assert tag >> 3 == want_field
                value, end = decode_leb128(pl, i + 1)
                assert value is not None
                assert end - (i + 1) == tag & 0x07   # low bits = varint length
                i = end
            checked += 1
    assert checked >= 60                  # both fixtures contribute


def test_marker_temperature_c_decodes_float32():
    # 020c telemetry sub-form carries a float32 LE water temperature (deg C).
    frame = _d807_telemetry(28.94, channel=2)
    assert len(frame) == 16
    assert abs(marker_temperature_c(frame) - 28.94) < 1e-3
    # the channel byte does not affect the reading (device-wide scalar)
    assert abs(marker_temperature_c(_d807_telemetry(28.94, channel=0)) - 28.94) < 1e-3
    # non-telemetry payloads yield None
    assert marker_temperature_c(_d807_delimiter(b'\xce\x57', channel=2)) is None
    assert marker_temperature_c(bytes([0xeb, 0x07, 0, 0]) + bytes(40)) is None
    assert marker_temperature_c(bytes([0xd8, 0x07])) is None        # truncated


def test_marker_temperature_c_decodes_real_capture_frame():
    # A telemetry frame captured verbatim from bag_2026-06-12T16.06.52 (pins the
    # real byte layout, not just the synthetic builder): 02 0c <f32> 19 <ch>.
    real = bytes.fromhex('d80700000800000002 0c057fe741 1900'.replace(' ', ''))
    assert len(real) == 16
    assert abs(marker_temperature_c(real) - 28.937) < 1e-2


def test_marker_temperature_c_rejects_non_finite():
    # A corrupt telemetry frame decoding to NaN/inf is not a usable reading --
    # None keeps it off the wire and out of the publisher's change-detection.
    nan_frame = (bytes([0xd8, 0x07, 0, 0]) + struct.pack('<I', 8)
                 + b'\x02\x0c' + b'\xff\xff\xff\xff' + b'\x19' + bytes([0]))
    assert marker_temperature_c(nan_frame) is None
    # ...but it is still structurally telemetry, so it must NOT flush the run.
    assert is_run_delimiter(nan_frame) is False


def test_is_run_delimiter_distinguishes_subforms():
    # The delimiter sub-form ends a run; the telemetry sub-form does not.
    assert is_run_delimiter(_d807_delimiter(b'\xce\x57', channel=2)) is True
    assert is_run_delimiter(_d807_telemetry(15.5, channel=1)) is False
    # an unrecognised/bare d807 still flushes (conservative); eb07 never does
    assert is_run_delimiter(bytes([0xd8, 0x07])) is True
    assert is_run_delimiter(bytes([0xeb, 0x07, 0, 0]) + bytes(40)) is False
    # a *truncated* telemetry frame (tag matches but too short for the float)
    # falls through to a flush rather than being silently swallowed
    truncated = bytes([0xd8, 0x07, 0, 0]) + struct.pack('<I', 8) + b'\x02\x0c\x05'
    assert is_run_delimiter(truncated) is True
    assert marker_temperature_c(truncated) is None


def test_assembler_keeps_ping_whole_across_telemetry_marker():
    # Regression for issue #37: a 020c temperature marker interleaved mid-run
    # must NOT split the ping. Build one channel's run as 4 packets + a telemetry
    # marker + 3 more packets, then a delimiter; expect ONE scan line of all 7.
    assembler = PingAssembler(dark_layer)   # synthetic dark packets (no FH/FHS)
    pkt = bytes([0xeb, 0x07, 0, 0]) + bytes(8) + bytes([5]) + bytes(20) \
        + bytes([174, 2, 172, 2]) + bytes([10, 20, 30])
    out = []
    for _ in range(4):
        out += assembler.feed(pkt, recv_time=100.0)
    out += assembler.feed(_d807_telemetry(15.5, channel=2))   # must not flush
    for _ in range(3):
        out += assembler.feed(pkt, recv_time=100.0)
    out += assembler.feed(_d807_delimiter(b'\xce\x57', channel=5))  # real boundary
    assert len(out) == 1                       # one whole ping, not two fragments
    assert out[0].channel == 5
    assert out[0].samples == bytes([10, 20, 30]) * 7


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
