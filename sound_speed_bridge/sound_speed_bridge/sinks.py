r"""
Output formatters for sound speed readings.

Each formatter takes (reading, template, ctx) and returns Optional[bytes]:
None means "skip emitting anything" (e.g. a NaN reading on a Valeport sink
that would emit garbage downstream).

- ``template`` is a per-target string used by the ``template`` formatter
  (Python str.format with backslash escapes processed). Other formatters
  ignore it.
- ``ctx`` is a small dict of node-level context (currently just
  ``frame_id``). Other formatters ignore it.

Formatters are registered by name in the FORMATTERS dict and selected per
UDP target via the udp_formats node parameter.
"""

import math
from typing import Callable, Dict, Optional

from .parsers import SoundSpeedReading

Formatter = Callable[[SoundSpeedReading, str, Dict[str, object]], Optional[bytes]]


def format_valeport(
    reading: SoundSpeedReading, template: str = '', ctx: Optional[dict] = None,
) -> Optional[bytes]:
    r"""
    Format a reading as Valeport-protocol UDP bytes.

    Emits ' NNNNNNN\r\n' -- space + 7-digit integer mm/s + CRLF. This is
    the format M3's built-in Valeport UDP driver expects.

    Skips NaN readings (would emit garbage). When raw_mm_s is available
    (from a parser that supplies bit-exact integer mm/s) it is used
    directly, preserving end-to-end bit-exactness; otherwise rounded from
    the float.
    """
    del template, ctx  # unused
    if reading.raw_mm_s is None:
        if math.isnan(reading.sound_speed_m_s):
            return None
        mm_s = round(reading.sound_speed_m_s * 1000)
    else:
        mm_s = reading.raw_mm_s
    if mm_s < 0 or mm_s > 9999999:
        return None
    return f' {mm_s:7d}\r\n'.encode('ascii')


def format_passthrough(
    reading: SoundSpeedReading, template: str = '', ctx: Optional[dict] = None,
) -> Optional[bytes]:
    """
    Relay the sensor's framed bytes verbatim.

    Use case: a downstream consumer that expects the sensor's native
    protocol (future QINSy AML driver, vendor display, debug tap). NaN
    readings are still relayed -- the whole point of passthrough is that
    downstream decides what to do with framed bytes, including ones the
    bridge couldn't parse.
    """
    del template, ctx  # unused
    return reading.raw_bytes if reading.raw_bytes else None


def format_template(
    reading: SoundSpeedReading, template: str = '', ctx: Optional[dict] = None,
) -> Optional[bytes]:
    r"""
    Format a reading using a user-supplied Python str.format template.

    Available substitutions:

    - ``{value}`` (float m/s)
    - ``{value_mm_s}`` (float, m/s * 1000)
    - ``{value_int_mm_s}`` (int; bit-exact when parser provided one,
      rounded from the float otherwise)
    - ``{stamp}`` (Unix epoch seconds, float)
    - ``{frame_id}`` (str, from the node's frame_id parameter)

    Backslash escapes (\\r, \\n, \\t) in the template are processed before
    formatting since YAML and CLI args do not naturally embed control chars.

    Skips NaN readings.
    """
    if not template:
        return None
    if math.isnan(reading.sound_speed_m_s):
        return None
    fmt = template.encode('ascii').decode('unicode_escape')
    int_mm_s = (reading.raw_mm_s if reading.raw_mm_s is not None
                else round(reading.sound_speed_m_s * 1000))
    frame_id = ''
    if isinstance(ctx, dict):
        frame_id = str(ctx.get('frame_id', ''))
    try:
        rendered = fmt.format(
            value=reading.sound_speed_m_s,
            value_mm_s=reading.sound_speed_m_s * 1000.0,
            value_int_mm_s=int_mm_s,
            stamp=reading.receive_time_ns / 1e9,
            frame_id=frame_id,
        )
    except (KeyError, IndexError, ValueError):
        return None
    return rendered.encode('ascii', errors='replace')


FORMATTERS: Dict[str, Formatter] = {
    'valeport': format_valeport,
    'passthrough': format_passthrough,
    'template': format_template,
}
