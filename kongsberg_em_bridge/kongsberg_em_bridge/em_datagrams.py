# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause
#
# The .all framing and XYZ88 decode are adapted from rolker/kongsberg_em
# (BSD). The Raw Range and Angle 78 (N) decode was added for the M3, whose
# XYZ88 datagram is exported empty (Nvalid=0) while N/78 carries the
# detections. See marine_tools#1.

"""
Pure-Python decoder for Kongsberg ``.all`` datagrams (no ROS dependency).

Only the datagrams needed for the M3 -> SonarDetections bridge are decoded in
full; others are recognised and returned as ``{'type': <id>}`` stubs. The
module is deliberately framework-free so it can be unit-tested directly and
reused outside ROS.
"""

from __future__ import annotations

import datetime
import struct
from typing import Iterator, Optional

STX = 0x02
ETX = 0x03

# .all datagram type bytes we care about.
DG_ATTITUDE = 0x41           # 'A'
DG_CLOCK = 0x43              # 'C'
DG_SURFACE_SOUND_SPEED = 0x47  # 'G'
DG_RAW_RANGE_ANGLE_78 = 0x4E   # 'N'  <- the one the M3 populates
DG_POSITION = 0x50           # 'P'
DG_XYZ88 = 0x58              # 'X'  <- empty on the M3, kept for EM2040/future

_UTC = datetime.timezone.utc


def em_time_to_unix(date: int, time_ms: int) -> Optional[float]:
    """
    Convert a Kongsberg date (YYYYMMDD int) + ms-since-midnight to Unix sec.

    Returns ``None`` if the date is zero/implausible so the caller can fall
    back to receive time.
    """
    if date < 19700101 or date > 30000101:
        return None
    if not 0 <= time_ms < 86_400_000:  # ms since midnight; reject corrupt values
        return None
    year, md = divmod(date, 10000)
    month, day = divmod(md, 100)
    try:
        midnight = datetime.datetime(year, month, day, tzinfo=_UTC)
    except ValueError:
        return None
    return midnight.timestamp() + time_ms / 1000.0


def parse_n78(p: bytes) -> dict:
    """
    Decode a Raw Range and Angle 78 ('N') datagram (payload starts at STX).

    Layout (little-endian): 32-byte header, then ``Ntx`` x 24-byte transmit
    sector blocks, then ``Nrx`` x 16-byte receive beam blocks. Validated
    against live M3 captures (Ntx=1, Nrx=229, +/-60.57 deg, ~9-10 m depths).
    """
    if len(p) < 32 or p[0] != STX or p[1] != DG_RAW_RANGE_ANGLE_78:
        raise ValueError('not an N/78 datagram')
    (model,) = struct.unpack_from('<H', p, 2)
    date, time_ms = struct.unpack_from('<II', p, 4)
    ping, serial = struct.unpack_from('<HH', p, 12)
    (ssp_raw,) = struct.unpack_from('<H', p, 16)        # 0.1 m/s
    ntx, nrx, nvalid = struct.unpack_from('<HHH', p, 18)
    (samp_freq,) = struct.unpack_from('<f', p, 24)

    # header + sectors + beams + trailer (spare/ETX/checksum). Require the full
    # datagram so a truncated one is rejected, not silently parsed as complete.
    expected = 32 + 24 * ntx + 16 * nrx + 4
    if len(p) < expected:
        raise ValueError(
            f'N/78 truncated: len={len(p)} need={expected} (ntx={ntx} nrx={nrx})')

    sectors = []
    off = 32
    for _ in range(ntx):
        (tilt_raw,) = struct.unpack_from('<h', p, off)        # 0.01 deg
        # sector: siglen[off+4], tx_delay[off+8], centre_freq[off+12] (float32)
        tx_delay, ctr_freq = struct.unpack_from('<ff', p, off + 8)
        sectors.append({
            'tilt_deg': tilt_raw * 0.01,
            'tx_delay': tx_delay,
            'centre_frequency': ctr_freq,
        })
        off += 24

    beams = []
    for _ in range(nrx):
        (angle_raw,) = struct.unpack_from('<h', p, off)       # 0.01 deg
        tx_sector, det_info = struct.unpack_from('<BB', p, off + 2)
        (twtt,) = struct.unpack_from('<f', p, off + 8)
        (refl_raw,) = struct.unpack_from('<h', p, off + 12)   # 0.1 dB
        beams.append({
            'pointing_angle_deg': angle_raw * 0.01,
            'tx_sector': tx_sector,
            'det_info': det_info,
            'twtt': twtt,
            'reflectivity_db': refl_raw * 0.1,
            # Kongsberg flags invalid detections with bit 7 of detection info.
            'valid': (det_info & 0x80) == 0,
        })
        off += 16

    return {
        'type': DG_RAW_RANGE_ANGLE_78,
        'model': model,
        'date': date,
        'time_ms': time_ms,
        'unix_time': em_time_to_unix(date, time_ms),
        'ping': ping,
        'serial': serial,
        'sound_speed': ssp_raw * 0.1,
        'sampling_frequency': samp_freq,
        'ntx': ntx,
        'nrx': nrx,
        'nvalid': nvalid,
        'sectors': sectors,
        'beams': beams,
    }


def parse_xyz88(p: bytes) -> dict:
    """
    Decode an XYZ88 ('X') datagram. Adapted from rolker/kongsberg_em.

    Kept for EM2040/other Kongsberg units; on the M3 this datagram is exported
    with zero valid detections, which is *why* the bridge uses N/78 instead.
    """
    if len(p) < 36 or p[0] != STX or p[1] != DG_XYZ88:
        raise ValueError('not an XYZ88 datagram')
    date, time_ms = struct.unpack_from('<II', p, 4)
    (ping,) = struct.unpack_from('<H', p, 12)
    nbeams, nvalid = struct.unpack_from('<HH', p, 24)
    expected = 36 + 20 * nbeams + 4  # header + beams + trailer
    if len(p) < expected:
        raise ValueError(
            f'XYZ88 truncated: len={len(p)} need={expected} (nbeams={nbeams})')
    beams = []
    for n in range(nbeams):
        base = 36 + 20 * n
        z, y, x = struct.unpack_from('<fff', p, base)
        # XYZ88 per-beam (20 B): ... window[12:14], quality[14], IBA[15],
        # detection info[16], cleaning[17], reflectivity[18:20].
        qf = p[base + 14]
        det_info = p[base + 16]
        beams.append({'x': x, 'y': y, 'z': z, 'quality': qf,
                      'valid': (det_info & 0x80) == 0})
    return {'type': DG_XYZ88, 'date': date, 'time_ms': time_ms,
            'unix_time': em_time_to_unix(date, time_ms), 'ping': ping,
            'nbeams': nbeams, 'nvalid': nvalid, 'beams': beams}


def parse_datagram(p: bytes) -> Optional[dict]:
    """Dispatch on datagram type. Returns ``None`` for an invalid frame."""
    if len(p) < 2 or p[0] != STX:
        return None
    dg = p[1]
    if dg == DG_RAW_RANGE_ANGLE_78:
        return parse_n78(p)
    if dg == DG_XYZ88:
        return parse_xyz88(p)
    return {'type': dg}  # recognised but not decoded


def iter_datagrams(buf: bytes) -> Iterator[bytes]:
    """
    Yield raw datagram payloads from a length-framed capture file.

    Framing: 4-byte big-endian length, then that many payload bytes. This is
    the format written by the ``m3_udp_capture.py`` dev tool, so captured
    samples can be replayed/tested offline.
    """
    i = 0
    n = len(buf)
    while i + 4 <= n:
        (ln,) = struct.unpack_from('>I', buf, i)
        i += 4
        if ln == 0 or i + ln > n:
            break
        yield buf[i:i + ln]
        i += ln
