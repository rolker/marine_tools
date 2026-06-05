"""
Garmin GCV TCP command frames (control port 50227).

Reverse-engineered by Dan Tauriello; verified live on both the GCV-10 and the
GCV-20 (2026-06-05).  Wire frame = ``d2 07 ef be`` + LE-length(4) + payload.

Transmit on/off is five frames (one per channel slot); the trailing byte is
``00`` for on and ``01`` for off.  Range is one frame per channel (1/2/3) with
the value as an unsigned base-128 varint in 0.5 mm units.
"""
import struct

# Five-frame transmit toggles; trailing a9 01 00 = on, a9 01 01 = off.
TRANSMIT_ON_HEX = (
    'd207efbe0a00000001070701020102a90100'
    'd207efbe0a00000001070701020104a90100'
    'd207efbe0a00000001070701020101a90100'
    'd207efbe0a00000001070701020103a90100'
    'd207efbe0a00000001070701020105a90100'
)
TRANSMIT_OFF_HEX = (
    'd207efbe0a00000001070701020102a90101'
    'd207efbe0a00000001070701020104a90101'
    'd207efbe0a00000001070701020101a90101'
    'd207efbe0a00000001070701020103a90101'
    'd207efbe0a00000001070701020105a90101'
)
TRANSMIT_ON = bytes.fromhex(TRANSMIT_ON_HEX)
TRANSMIT_OFF = bytes.fromhex(TRANSMIT_OFF_HEX)

RANGE_UNIT_M = 0.0005           # range field is in 0.5 mm units

# Enum order for the TVG / interference level controls (index = wire value).
LOW_MED_HIGH = ('off', 'low', 'medium', 'high')


def _frame(payload):
    """Wrap a command payload in the d2 07 ef be + LE-length envelope."""
    return b'\xd2\x07\xef\xbe' + struct.pack('<I', len(payload)) + payload


def build_tvg_cmd(level):
    """Build a TVG command frame for level 0..3 (off/low/medium/high)."""
    level = max(0, min(3, int(level)))
    return _frame(bytes([0x01, 0x07, 0x07, 0x01, 0x02, 0x01, 0x00, 0x89, 0x01, level]))


def build_interference_cmd(level):
    """Build an interference-rejection frame for level 0..3."""
    level = max(0, min(3, int(level)))
    return _frame(bytes([0x01, 0x07, 0x07, 0x01, 0x02, 0x01, 0x00, 0xa1, 0x01, level]))


def encode_leb128(value):
    """Encode a non-negative int as an unsigned base-128 varint (LE groups)."""
    value = int(value)
    if value < 0:
        raise ValueError('value must be non-negative')
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def build_range_cmd(meters):
    """Build the range command (one frame per channel 1/2/3) for ``meters``."""
    units = max(0, int(round(meters / RANGE_UNIT_M)))
    varint = encode_leb128(units)
    frames = bytearray()
    for ch in (1, 2, 3):
        payload = bytes([0x01, 0x07, 0x08, 0x01, 0x02, 0x01, ch, 0x5b]) + varint
        frames += b'\xd2\x07\xef\xbe' + struct.pack('<I', len(payload)) + payload
    return bytes(frames)
