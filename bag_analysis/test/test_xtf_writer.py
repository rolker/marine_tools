"""
Tests for bag_analysis.xtf.writer.

Validates the written XTF binary by parsing it back with plain ``struct``
(no third-party dependency), then, if ``pyxtf`` happens to be installed,
cross-checks that an independent reader agrees on ping count and samples.
"""

import datetime as _dt
import io
import struct

from bag_analysis.xtf.writer import ChannelPing, XtfWriter
import numpy as np
import pytest

_FILE_HEADER_LEN = 1024
_PING_HEADER_LEN = 256
_PING_CHAN_HEADER_LEN = 64


def _chan(values, slant_range=20.0, freq=455000.0):
    return ChannelPing(
        samples=np.asarray(values, dtype=np.uint16),
        slant_range_m=slant_range, frequency_hz=freq,
        seconds_per_ping=0.08, time_delay_s=0.0002, time_duration_s=0.026)


def _write_two_pings() -> bytes:
    stream = io.BytesIO()
    writer = XtfWriter(stream, sonar_name='Test Sidescan',
                       port_frequency_hz=455000.0,
                       starboard_frequency_hz=455000.0)
    t = _dt.datetime(2026, 6, 15, 15, 3, 45, 120000, tzinfo=_dt.timezone.utc)
    writer.write_ping(
        time=t, ping_number=0, latitude_deg=42.990559,
        longitude_deg=-71.392957, sensor_depth_m=0.0, altitude_m=3.5,
        heading_deg=123.4, pitch_deg=1.2, roll_deg=-2.3, speed_mps=1.5,
        sound_velocity_mps=1500.0,
        port=_chan([10, 20, 30, 40]), starboard=_chan([50, 60, 70, 80]))
    writer.write_ping(
        time=t, ping_number=1, latitude_deg=42.990600,
        longitude_deg=-71.392900, sensor_depth_m=0.0, altitude_m=3.6,
        heading_deg=124.0, pitch_deg=1.0, roll_deg=-2.0, speed_mps=1.6,
        sound_velocity_mps=1500.0,
        port=_chan([11, 21, 31]), starboard=_chan([51, 61, 71]))
    assert writer.ping_count == 2
    return stream.getvalue()


def test_file_header_fields():
    data = _write_two_pings()
    assert len(data) >= _FILE_HEADER_LEN
    assert data[0] == 0x7B                                  # FileFormat
    assert struct.unpack_from('<H', data, 164)[0] == 3      # NavUnits lat/lon
    assert struct.unpack_from('<H', data, 166)[0] == 2      # NumSonarChannels
    # CHANINFO[0] = port, CHANINFO[1] = starboard.
    assert data[256 + 0] == 1                               # port channel
    assert struct.unpack_from('<H', data, 256 + 6)[0] == 2  # BytesPerSample
    assert data[384 + 0] == 2                               # stbd channel
    freq = struct.unpack_from('<f', data, 256 + 32)[0]
    assert freq == pytest.approx(455000.0)


def _parse_pings(data: bytes):
    """Walk ping packets using NumBytesThisRecord; yield parsed dicts."""
    offset = _FILE_HEADER_LEN
    pings = []
    while offset < len(data):
        magic = struct.unpack_from('<H', data, offset)[0]
        assert magic == 0xFACE
        record_len = struct.unpack_from('<I', data, offset + 10)[0]
        n_chans = struct.unpack_from('<H', data, offset + 4)[0]
        ping = {
            'lat': struct.unpack_from('<d', data, offset + 128)[0],
            'lon': struct.unpack_from('<d', data, offset + 136)[0],
            'sensor_lat': struct.unpack_from('<d', data, offset + 160)[0],
            'altitude': struct.unpack_from('<f', data, offset + 196)[0],
            'heading': struct.unpack_from('<f', data, offset + 212)[0],
            'year': struct.unpack_from('<H', data, offset + 14)[0],
            'channels': [],
        }
        pos = offset + _PING_HEADER_LEN
        for _ in range(n_chans):
            n_samples = struct.unpack_from('<I', data, pos + 42)[0]
            slant = struct.unpack_from('<f', data, pos + 4)[0]
            sample_start = pos + _PING_CHAN_HEADER_LEN
            samples = np.frombuffer(
                data, dtype='<u2', count=n_samples, offset=sample_start)
            ping['channels'].append(
                {'n': n_samples, 'slant': slant, 'samples': samples})
            pos = sample_start + n_samples * 2
        pings.append(ping)
        offset += record_len
    return pings


def test_ping_packets_round_trip():
    data = _write_two_pings()
    pings = _parse_pings(data)
    assert len(pings) == 2

    first = pings[0]
    assert first['lat'] == pytest.approx(42.990559)
    assert first['lon'] == pytest.approx(-71.392957)
    assert first['sensor_lat'] == pytest.approx(42.990559)
    assert first['altitude'] == pytest.approx(3.5)
    assert first['heading'] == pytest.approx(123.4, abs=1e-3)
    assert first['year'] == 2026
    assert len(first['channels']) == 2
    # Port (channel 0) is stored reversed per XTF convention; starboard as-is.
    assert list(first['channels'][0]['samples']) == [40, 30, 20, 10]
    assert list(first['channels'][1]['samples']) == [50, 60, 70, 80]
    assert first['channels'][0]['slant'] == pytest.approx(20.0)
    # The per-ping Frequency uint16 (offset 26) can't hold sidescan
    # frequencies, so it is deliberately left 0 rather than truncated.
    chan0 = 1024 + 256
    assert struct.unpack_from('<H', data, chan0 + 26)[0] == 0

    second = pings[1]
    assert list(second['channels'][0]['samples']) == [31, 21, 11]  # port reversed
    assert list(second['channels'][1]['samples']) == [51, 61, 71]


def test_port_channel_reversed_starboard_unchanged():
    """Port samples are written reversed (XTF convention); starboard is not."""
    stream = io.BytesIO()
    writer = XtfWriter(stream)
    t = _dt.datetime(2026, 6, 15, tzinfo=_dt.timezone.utc)
    writer.write_ping(
        time=t, ping_number=0, latitude_deg=0.0, longitude_deg=0.0,
        sensor_depth_m=0.0, altitude_m=0.0, heading_deg=0.0, pitch_deg=0.0,
        roll_deg=0.0, speed_mps=0.0, sound_velocity_mps=1500.0,
        port=_chan([1, 2, 3, 4, 5]), starboard=_chan([1, 2, 3, 4, 5]))
    ping = _parse_pings(stream.getvalue())[0]
    assert list(ping['channels'][0]['samples']) == [5, 4, 3, 2, 1]  # port flipped
    assert list(ping['channels'][1]['samples']) == [1, 2, 3, 4, 5]  # stbd as-is


def test_record_length_consumes_exact_file():
    # Walking by NumBytesThisRecord must land exactly on EOF.
    data = _write_two_pings()
    offset = _FILE_HEADER_LEN
    while offset < len(data):
        offset += struct.unpack_from('<I', data, offset + 10)[0]
    assert offset == len(data)


def test_clipping_out_of_range_samples():
    stream = io.BytesIO()
    writer = XtfWriter(stream)
    t = _dt.datetime(2026, 6, 15, tzinfo=_dt.timezone.utc)
    writer.write_ping(
        time=t, ping_number=0, latitude_deg=0.0, longitude_deg=0.0,
        sensor_depth_m=0.0, altitude_m=0.0, heading_deg=0.0, pitch_deg=0.0,
        roll_deg=0.0, speed_mps=0.0, sound_velocity_mps=1500.0,
        port=ChannelPing(samples=np.array([0, 70000, -5], dtype=np.int64),
                         slant_range_m=10.0),
        starboard=ChannelPing(samples=np.array([1, 2, 3], dtype=np.int64),
                              slant_range_m=10.0))
    pings = _parse_pings(stream.getvalue())
    assert list(pings[0]['channels'][0]['samples']) == [0, 65535, 0]


def test_pyxtf_cross_check_if_available():
    pyxtf = pytest.importorskip('pyxtf')
    import tempfile
    import os
    data = _write_two_pings()
    with tempfile.NamedTemporaryFile(
            suffix='.xtf', delete=False) as handle:
        handle.write(data)
        path = handle.name
    try:
        _fh, packets = pyxtf.xtf_read(path)
        sonar = packets.get(pyxtf.XTFHeaderType.sonar, [])
        assert len(sonar) == 2
    finally:
        os.unlink(path)
