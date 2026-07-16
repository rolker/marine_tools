# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""Unit tests for the framework-free Kongsberg .all decoder."""

import struct

from kongsberg_em_bridge import em_datagrams as em


def _build_n78(beams, *, ntx_tilt_deg=0.0, ctr_freq=500000.0, tx_delay=0.001,
               sound_speed=1490.9, date=20260604, time_ms=1000, ping=7,
               siglen=0.0001, waveform=0, bandwidth=12000.0):
    """
    Construct a synthetic Raw Range and Angle 78 datagram.

    ``beams`` is a list of (angle_deg, det_info, twtt, refl_db).
    """
    nrx = len(beams)
    header = struct.pack(
        '<BBHIIHHHHHHfI',
        em.STX, em.DG_RAW_RANGE_ANGLE_78,
        30,                 # model
        date, time_ms,
        ping, 1,            # ping counter, serial
        round(sound_speed * 10),
        1, nrx, nrx,        # ntx, nrx, nvalid
        48828.0,            # sampling frequency
        0)                  # dscale
    sector = struct.pack(
        '<hHfffHBBf',
        round(ntx_tilt_deg * 100), 0,       # tilt 0.01 deg, focus range
        siglen, tx_delay, ctr_freq,         # siglen, tx delay, centre freq
        0, waveform, 0, bandwidth)          # mean abs, waveform, sector#, bw
    body = b''
    for angle_deg, det_info, twtt, refl_db in beams:
        body += struct.pack(
            '<hBBHBbfhbB',
            round(angle_deg * 100), 0, det_info, 0,   # angle, sector, det, win
            10, 0,                                    # quality, dcorr
            twtt,
            round(refl_db * 10), 0, 0)                # refl, rt clean, spare
    frame = header + sector + body + b'\x00'           # ... + spare byte
    cksum = sum(frame[1:]) & 0xFFFF                     # sum between STX and ETX
    return frame + bytes([em.ETX]) + struct.pack('<H', cksum)


def test_parse_n78_basic():
    dg = _build_n78([
        (-30.0, 0x00, 0.020, -28.0),    # valid
        (+30.0, 0x80, 0.025, -31.0),    # invalid (bit 7 set)
    ])
    out = em.parse_n78(dg)
    assert out['type'] == em.DG_RAW_RANGE_ANGLE_78
    assert out['ntx'] == 1 and out['nrx'] == 2
    assert abs(out['sound_speed'] - 1490.9) < 0.05
    assert abs(out['sectors'][0]['centre_frequency'] - 500000.0) < 1.0
    assert abs(out['sectors'][0]['tx_delay'] - 0.001) < 1e-6
    # Acquisition fields for SonarInfo (marine_tools#69): builder defaults.
    assert abs(out['sectors'][0]['signal_length'] - 0.0001) < 1e-9
    assert out['sectors'][0]['waveform'] == 0
    assert abs(out['sectors'][0]['bandwidth'] - 12000.0) < 1e-3
    b0, b1 = out['beams']
    assert abs(b0['pointing_angle_deg'] - (-30.0)) < 1e-3
    assert abs(b0['twtt'] - 0.020) < 1e-6
    assert b0['valid'] is True
    assert b1['valid'] is False        # det_info bit 7 set
    assert out['unix_time'] is not None


def test_parse_n78_acquisition_fields_roundtrip():
    # Non-default siglen/waveform/bandwidth survive the sector decode
    # (FM up sweep; values in seconds / spec id / Hz).
    dg = _build_n78([(0.0, 0x00, 0.020, -25.0)],
                    siglen=0.0025, waveform=1, bandwidth=30000.0)
    sector = em.parse_n78(dg)['sectors'][0]
    assert abs(sector['signal_length'] - 0.0025) < 1e-9
    assert sector['waveform'] == 1
    assert abs(sector['bandwidth'] - 30000.0) < 1e-3


def test_parse_n78_rejects_wrong_type():
    bad = bytes([em.STX, em.DG_XYZ88]) + b'\x00' * 40
    try:
        em.parse_n78(bad)
        assert False, 'expected ValueError'
    except ValueError:
        pass


def test_dispatch_and_iter_framing():
    dg = _build_n78([(0.0, 0x00, 0.0125, -25.0)])
    framed = struct.pack('>I', len(dg)) + dg + struct.pack('>I', 2) + b'\x02\x41'
    payloads = list(em.iter_datagrams(framed))
    assert len(payloads) == 2
    parsed = em.parse_datagram(payloads[0])
    assert parsed['type'] == em.DG_RAW_RANGE_ANGLE_78
    # second is a recognised-but-undecoded attitude stub
    assert em.parse_datagram(payloads[1])['type'] == em.DG_ATTITUDE


def test_frame_all_record_little_endian():
    # Genuine .all framing is a little-endian length prefix == len(payload).
    payload = bytes([em.STX, em.DG_ATTITUDE]) + b'\xaa\xbb\xcc'
    rec = em.frame_all_record(payload)
    assert rec == struct.pack('<I', len(payload)) + payload
    assert struct.unpack_from('<I', rec, 0)[0] == len(payload)
    assert rec[4:] == payload


def test_frame_all_record_distinct_from_big_endian_capture():
    # The saved .all length is little-endian and must differ from the repo's
    # big-endian capture framing for any non-byte-symmetric length.
    payload = b'\x02\x41' + b'\x00' * 258  # len 260 -> 0x0104, asymmetric
    assert em.frame_all_record(payload)[:4] == struct.pack('<I', len(payload))
    assert struct.pack('<I', len(payload)) != struct.pack('>I', len(payload))


def test_frame_all_record_roundtrips_with_le_reader():
    # A minimal little-endian reader recovers the original datagrams, proving
    # the saved file is parseable as a real .all stream.
    dg = _build_n78([(0.0, 0x00, 0.0125, -25.0)])
    aux = bytes([em.STX, em.DG_POSITION]) + b'\x01\x02\x03'
    blob = em.frame_all_record(dg) + em.frame_all_record(aux)
    recovered = []
    i = 0
    while i + 4 <= len(blob):
        (ln,) = struct.unpack_from('<I', blob, i)
        i += 4
        recovered.append(blob[i:i + ln])
        i += ln
    assert recovered == [dg, aux]
    assert em.parse_datagram(recovered[0])['type'] == em.DG_RAW_RANGE_ANGLE_78


def test_em_time_to_unix():
    assert em.em_time_to_unix(0, 0) is None
    assert em.em_time_to_unix(20260604, 86_400_000) is None  # time_ms out of range
    assert em.em_time_to_unix(20260604, -1) is None
    t = em.em_time_to_unix(20260604, 1000)
    assert t is not None and t > 1.7e9


def test_truncated_datagrams_raise():
    n78 = _build_n78([(0.0, 0x00, 0.0125, -25.0)])
    try:
        em.parse_n78(n78[:-4])  # drop the spare/ETX/checksum trailer
        assert False, 'expected ValueError on truncated N/78'
    except ValueError:
        pass
    xyz = _build_xyz88([(10.0, 0.0, 0.0, 30, 0x00)])
    try:
        em.parse_xyz88(xyz[:-4])
        assert False, 'expected ValueError on truncated XYZ88'
    except ValueError:
        pass


def test_bad_etx_raises():
    n78 = bytearray(_build_n78([(0.0, 0x00, 0.0125, -25.0)]))
    n78[-3] = 0x00  # corrupt the ETX byte (right length, wrong trailer)
    try:
        em.parse_n78(bytes(n78))
        assert False, 'expected ValueError on bad N/78 ETX'
    except ValueError:
        pass


def test_bad_checksum_raises():
    n78 = bytearray(_build_n78([(10.0, 0x00, 0.0125, -25.0)]))
    n78[-4] ^= 0xFF  # corrupt the spare byte (in checksum range, not structural)
    try:
        em.parse_n78(bytes(n78))
        assert False, 'expected ValueError on N/78 checksum mismatch'
    except ValueError:
        pass


def _build_xyz88(beams):
    """
    Construct a synthetic XYZ88 datagram.

    ``beams`` is a list of (z, y, x, quality, det_info). Per-beam block is
    20 B: z/y/x float32 [0:12], quality at 14, IBA at 15, detection info at 16.
    """
    nb = len(beams)
    header = bytearray(36)
    header[0] = em.STX
    header[1] = em.DG_XYZ88
    struct.pack_into('<II', header, 4, 20260604, 1000)  # date, time_ms
    struct.pack_into('<H', header, 12, 7)               # ping
    struct.pack_into('<HH', header, 24, nb, nb)         # nbeams, nvalid
    body = b''
    for z, y, x, qf, det in beams:
        b = bytearray(20)
        struct.pack_into('<fff', b, 0, z, y, x)
        b[14] = qf          # quality
        b[15] = 7           # IBA (must NOT be read as det_info)
        b[16] = det         # detection info (bit 7 => invalid)
        body += bytes(b)
    frame = bytes(header) + body + b'\x00'              # ... + spare byte
    cksum = sum(frame[1:]) & 0xFFFF
    return frame + bytes([em.ETX]) + struct.pack('<H', cksum)


def test_parse_xyz88_validity_and_offset():
    dg = _build_xyz88([
        (12.5, -3.0, 0.1, 30, 0x00),   # valid
        (13.0, 3.0, 0.1, 28, 0x80),    # bit 7 set => invalid
    ])
    out = em.parse_xyz88(dg)
    assert out['nbeams'] == 2
    b0, b1 = out['beams']
    assert abs(b0['z'] - 12.5) < 1e-4 and abs(b0['y'] - (-3.0)) < 1e-4
    # det_info must be read at offset 16, not 15 (IBA=7 there would read valid).
    assert b0['valid'] is True
    assert b1['valid'] is False
