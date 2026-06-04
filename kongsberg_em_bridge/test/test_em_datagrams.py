# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""Unit tests for the framework-free Kongsberg .all decoder."""

import struct

from kongsberg_em_bridge import em_datagrams as em


def _build_n78(beams, *, ntx_tilt_deg=0.0, ctr_freq=500000.0, tx_delay=0.001,
               sound_speed=1490.9, date=20260604, time_ms=1000, ping=7):
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
        0.0001, tx_delay, ctr_freq,         # siglen, tx delay, centre freq
        0, 0, 0, 12000.0)                   # mean abs, waveform, sector#, bw
    body = b''
    for angle_deg, det_info, twtt, refl_db in beams:
        body += struct.pack(
            '<hBBHBbfhbB',
            round(angle_deg * 100), 0, det_info, 0,   # angle, sector, det, win
            10, 0,                                    # quality, dcorr
            twtt,
            round(refl_db * 10), 0, 0)                # refl, rt clean, spare
    trailer = struct.pack('<BBH', 0, em.ETX, 0)       # spare, ETX, checksum
    return header + sector + body + trailer


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
    b0, b1 = out['beams']
    assert abs(b0['pointing_angle_deg'] - (-30.0)) < 1e-3
    assert abs(b0['twtt'] - 0.020) < 1e-6
    assert b0['valid'] is True
    assert b1['valid'] is False        # det_info bit 7 set
    assert out['unix_time'] is not None


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


def test_em_time_to_unix():
    assert em.em_time_to_unix(0, 0) is None
    t = em.em_time_to_unix(20260604, 1000)
    assert t is not None and t > 1.7e9
