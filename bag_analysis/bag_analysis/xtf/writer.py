"""
Streaming binary writer for XTF (eXtended Triton Format) sidescan files.

XTF is a little-endian binary format: a fixed 1024-byte file header
(256 bytes of global fields followed by six 128-byte ``CHANINFO``
blocks), then a stream of ping packets. Each sonar ping packet is a
256-byte ``XTFPINGHEADER`` followed by, for every channel, a 64-byte
``XTFPINGCHANHEADER`` and that channel's raw amplitude samples.

This writer targets the common two-channel sidescan layout: channel 0 =
port, channel 1 = starboard, both 16-bit unsigned samples. It writes the
header on construction and appends ping packets one at a time, so memory
stays bounded regardless of bag length.

Field offsets and semantics follow the Triton XTF specification (the
same layout the ``pyxtf`` reader implements). Only the fields a sidescan
mosaic actually needs are populated; the remainder are left zero.

References
----------
* Triton Imaging XTF File Format specification.
* https://en.wikipedia.org/wiki/EXtended_Triton_Format

"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as _dt
import struct
from typing import BinaryIO

import numpy as np

# --- Constant magic / enum values from the XTF spec ----------------------
_FILE_FORMAT = 0x7B          # XTFFILEHEADER.FileFormat (123)
_SYSTEM_TYPE = 1             # generic
_PING_MAGIC = 0xFACE         # XTFPINGHEADER.MagicNumber
_HEADER_TYPE_SONAR = 0       # XTFPINGHEADER.HeaderType for sonar pings
_NAV_UNITS_LATLON = 3        # XTFFILEHEADER.NavUnits: 3 => lat/lon degrees
_CHAN_TYPE_PORT = 1          # CHANINFO.TypeOfChannel
_CHAN_TYPE_STBD = 2
_BYTES_PER_SAMPLE = 2        # 16-bit samples

_FILE_HEADER_LEN = 1024
_CHAN_INFO_LEN = 128
_CHAN_INFO_BASE = 256        # first CHANINFO starts here
_PING_HEADER_LEN = 256
_PING_CHAN_HEADER_LEN = 64

_SAMPLE_DTYPE = np.dtype('<u2')  # little-endian uint16

# Per-ping channel layouts (see XtfWriter for the trade-off).
LAYOUT_STANDARD = 'standard'      # interleaved [hdr0][data0][hdr1][data1]
LAYOUT_PINGMAPPER = 'pingmapper'  # contiguous  [hdr0][hdr1][data0][data1]


@dataclass
class ChannelPing:
    """One channel's data for a single ping."""

    samples: np.ndarray   # 1-D, will be cast to little-endian uint16
    slant_range_m: float  # range to the last sample, metres
    frequency_hz: float = 0.0
    seconds_per_ping: float = 0.0
    time_delay_s: float = 0.0   # one-way? -> round-trip time to first sample
    time_duration_s: float = 0.0


class XtfWriter:
    """Write a two-channel (port/starboard) sidescan XTF file incrementally."""

    def __init__(
        self,
        stream: BinaryIO,
        *,
        sonar_name: str = 'sidescan',
        program_name: str = 'bag_to_xtf',
        note: str = '',
        port_frequency_hz: float = 0.0,
        starboard_frequency_hz: float = 0.0,
        layout: str = LAYOUT_STANDARD,
    ) -> None:
        """
        Open the writer and emit the XTF file header to ``stream``.

        ``layout`` controls the per-ping channel arrangement:

        * ``LAYOUT_STANDARD`` (default) -- spec-compliant interleaved layout:
          each XTFPINGCHANHEADER is immediately followed by its own channel's
          samples (``[hdr0][data0][hdr1][data1]``). This is what the Triton XTF
          spec and the reference reader (pyxtf) require.
        * ``LAYOUT_PINGMAPPER`` -- non-standard contiguous layout that groups
          both channel headers first, then both channels' samples
          (``[hdr0][hdr1][data0][data1]``). PINGVerter / PING-Mapper assume
          this arrangement and misread the standard interleaved layout for the
          second channel; use it only when the output is destined for that
          toolchain. Standard XTF readers will mis-decode it.
        """
        if layout not in (LAYOUT_STANDARD, LAYOUT_PINGMAPPER):
            raise ValueError(
                f'unknown layout {layout!r}; expected '
                f'{LAYOUT_STANDARD!r} or {LAYOUT_PINGMAPPER!r}')
        self._stream = stream
        self._layout = layout
        self._ping_count = 0
        self._write_file_header(
            sonar_name, program_name, note,
            port_frequency_hz, starboard_frequency_hz,
        )

    @property
    def ping_count(self) -> int:
        """Return the number of ping packets written so far."""
        return self._ping_count

    # -- file header ------------------------------------------------------
    def _write_file_header(
        self,
        sonar_name: str,
        program_name: str,
        note: str,
        port_freq: float,
        stbd_freq: float,
    ) -> None:
        """Write the 1024-byte XTF file header and channel-info blocks."""
        buf = bytearray(_FILE_HEADER_LEN)
        struct.pack_into('<B', buf, 0, _FILE_FORMAT)
        struct.pack_into('<B', buf, 1, _SYSTEM_TYPE)
        _pack_string(buf, 2, program_name, 8)    # RecordingProgramName
        _pack_string(buf, 10, '1.0', 8)          # RecordingProgramVersion
        _pack_string(buf, 18, sonar_name, 16)    # SonarName
        _pack_string(buf, 36, note, 64)          # NoteString
        struct.pack_into('<H', buf, 164, _NAV_UNITS_LATLON)   # NavUnits
        struct.pack_into('<H', buf, 166, 2)      # NumberOfSonarChannels
        # CHANINFO[0] = port, CHANINFO[1] = starboard.
        self._pack_chan_info(buf, 0, _CHAN_TYPE_PORT, 'PORT', port_freq)
        self._pack_chan_info(buf, 1, _CHAN_TYPE_STBD, 'STBD', stbd_freq)
        self._stream.write(buf)

    @staticmethod
    def _pack_chan_info(
        buf: bytearray,
        index: int,
        type_of_channel: int,
        name: str,
        frequency_hz: float,
    ) -> None:
        base = _CHAN_INFO_BASE + index * _CHAN_INFO_LEN
        struct.pack_into('<B', buf, base + 0, type_of_channel)  # TypeOfChannel
        struct.pack_into('<B', buf, base + 1, index)            # SubChannel
        struct.pack_into('<H', buf, base + 6, _BYTES_PER_SAMPLE)
        _pack_string(buf, base + 12, name, 16)                 # ChannelName
        struct.pack_into('<f', buf, base + 28, 1.0)            # VoltScale
        struct.pack_into('<f', buf, base + 32, float(frequency_hz))

    # -- ping packets -----------------------------------------------------
    def write_ping(
        self,
        *,
        time: _dt.datetime,
        ping_number: int,
        latitude_deg: float,
        longitude_deg: float,
        sensor_depth_m: float,
        altitude_m: float,
        heading_deg: float,
        pitch_deg: float,
        roll_deg: float,
        speed_mps: float,
        sound_velocity_mps: float,
        port: ChannelPing,
        starboard: ChannelPing,
    ) -> None:
        """Append one two-channel sonar ping packet to the file."""
        # XTF convention: the port channel is stored in reversed sample order
        # (outermost/far range first, nadir last) relative to starboard, so a
        # standard viewer renders port extending left of nadir into a
        # continuous swath. The driver publishes both channels near->far, so
        # flip port here; starboard is written as-is.
        port_bytes = _samples_to_bytes(np.asarray(port.samples)[::-1])
        stbd_bytes = _samples_to_bytes(starboard.samples)
        n_port = len(port_bytes) // _BYTES_PER_SAMPLE
        n_stbd = len(stbd_bytes) // _BYTES_PER_SAMPLE

        num_bytes_record = (
            _PING_HEADER_LEN
            + 2 * _PING_CHAN_HEADER_LEN
            + len(port_bytes) + len(stbd_bytes)
        )

        header = bytearray(_PING_HEADER_LEN)
        struct.pack_into('<H', header, 0, _PING_MAGIC)
        struct.pack_into('<B', header, 2, _HEADER_TYPE_SONAR)
        struct.pack_into('<H', header, 4, 2)            # NumChansToFollow
        struct.pack_into('<I', header, 10, num_bytes_record)
        struct.pack_into('<H', header, 14, time.year)
        struct.pack_into('<B', header, 16, time.month)
        struct.pack_into('<B', header, 17, time.day)
        struct.pack_into('<B', header, 18, time.hour)
        struct.pack_into('<B', header, 19, time.minute)
        struct.pack_into('<B', header, 20, time.second)
        struct.pack_into('<B', header, 21, time.microsecond // 10000)  # hsec
        struct.pack_into('<I', header, 28, ping_number & 0xFFFFFFFF)
        struct.pack_into('<f', header, 32, float(sound_velocity_mps))
        struct.pack_into('<f', header, 120, float(speed_mps))   # ShipSpeed
        struct.pack_into('<f', header, 124, float(heading_deg))  # ShipGyro
        struct.pack_into('<d', header, 128, float(latitude_deg))   # ShipY
        struct.pack_into('<d', header, 136, float(longitude_deg))  # ShipX
        struct.pack_into('<f', header, 152, float(speed_mps))   # SensorSpeed
        struct.pack_into('<d', header, 160, float(latitude_deg))   # SensorY
        struct.pack_into('<d', header, 168, float(longitude_deg))  # SensorX
        struct.pack_into('<f', header, 192, float(sensor_depth_m))
        struct.pack_into('<f', header, 196, float(altitude_m))  # Altitude
        struct.pack_into('<f', header, 204, float(pitch_deg))
        struct.pack_into('<f', header, 208, float(roll_deg))
        struct.pack_into('<f', header, 212, float(heading_deg))

        port_header = self._chan_header(0, n_port, port)
        stbd_header = self._chan_header(1, n_stbd, starboard)

        self._stream.write(header)
        if self._layout == LAYOUT_PINGMAPPER:
            # Contiguous: both channel headers, then both channels' samples.
            self._stream.write(port_header)
            self._stream.write(stbd_header)
            self._stream.write(port_bytes)
            self._stream.write(stbd_bytes)
        else:
            # Standard interleaved: each header immediately followed by its data.
            self._stream.write(port_header)
            self._stream.write(port_bytes)
            self._stream.write(stbd_header)
            self._stream.write(stbd_bytes)
        self._ping_count += 1

    @staticmethod
    def _chan_header(
        channel_number: int, num_samples: int, chan: ChannelPing,
    ) -> bytes:
        buf = bytearray(_PING_CHAN_HEADER_LEN)
        struct.pack_into('<H', buf, 0, channel_number)
        struct.pack_into('<f', buf, 4, float(chan.slant_range_m))  # SlantRange
        struct.pack_into('<f', buf, 12, float(chan.time_delay_s))
        struct.pack_into('<f', buf, 16, float(chan.time_duration_s))
        struct.pack_into('<f', buf, 20, float(chan.seconds_per_ping))
        # The per-ping Frequency field here is a uint16 (Hz) that cannot
        # represent sidescan frequencies (e.g. 455 kHz), so it is left 0;
        # the authoritative per-channel frequency lives in CHANINFO
        # (a float) written in the file header.
        struct.pack_into('<I', buf, 42, num_samples)              # NumSamples
        return bytes(buf)


def _samples_to_bytes(samples: np.ndarray) -> bytes:
    """Cast a 1-D sample array to little-endian uint16 bytes (clipped)."""
    arr = np.asarray(samples)
    if arr.ndim != 1:
        arr = arr.reshape(-1)
    arr = np.clip(arr, 0, 65535).astype(_SAMPLE_DTYPE, copy=False)
    return arr.tobytes()


def _pack_string(buf: bytearray, offset: int, value: str, length: int) -> None:
    """Write a fixed-width, NUL-padded ASCII string into ``buf``."""
    encoded = value.encode('ascii', errors='replace')[:length]
    buf[offset:offset + len(encoded)] = encoded
