r"""
Output formatters for sound speed readings.

Each formatter takes a SoundSpeedReading and returns Optional[bytes]: None
means "skip emitting anything" (e.g. a NaN reading on a Valeport sink that
would emit garbage downstream).

Formatters are registered by name in the FORMATTERS dict and selected per
UDP target via the udp_formats node parameter.
"""

import math
from typing import Callable, Optional

from .parsers import SoundSpeedReading

Formatter = Callable[[SoundSpeedReading], Optional[bytes]]


def format_valeport(reading: SoundSpeedReading) -> Optional[bytes]:
    r"""
    Format a reading as Valeport-protocol UDP bytes.

    Emits ' NNNNNNN\r\n' -- space + 7-digit integer mm/s + CRLF. This is
    the format M3's built-in Valeport UDP driver expects.

    Skips NaN readings (would emit garbage). When raw_mm_s is available
    (from a parser that supplies bit-exact integer mm/s) it is used
    directly, preserving end-to-end bit-exactness; otherwise rounded from
    the float.
    """
    if reading.raw_mm_s is None:
        if math.isnan(reading.sound_speed_m_s):
            return None
        mm_s = round(reading.sound_speed_m_s * 1000)
    else:
        mm_s = reading.raw_mm_s
    if mm_s < 0 or mm_s > 9999999:
        return None
    return f' {mm_s:7d}\r\n'.encode('ascii')


FORMATTERS: dict = {
    'valeport': format_valeport,
}
