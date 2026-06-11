"""
Garmin GCV-10/20 sidescan imagery decode primitives.

  eb07 packet = magic ``eb 07`` ``00 00`` + LE-length(4) + 12-byte sub-header
    + render layers.  Channel byte is at payload offset 12.
  d807 packet = marker delimiting a channel's run within a ping.
  One channel scan line = 7 consecutive same-channel packets (6 full + 1 short).

Per-packet layers are bracketed by header signatures: ``fh``/``fhs`` opens the
first layer, ``sh``/``shs`` opens later layers.  The high-resolution echo lives
in a DIFFERENT layer per generation, so the per-packet sample extractor is a
parameter of :class:`PingAssembler`, chosen by the caller from the device
generation (see node.py's ``device`` auto-detect):

* **GCV-10** (validated against Dan's survey capture, ``decoded2_sidescan.png``):
  three layers per packet; the echo is the LAST ("dark") layer -- bytes after
  the final ``sh``/``shs`` + 4 to end of packet.  Use :func:`dark_layer`.
* **GCV-20** (bench-validated 2026-06-07 against the GCV-10 bucket reference;
  pending a wet-capture seafloor confirmation): only two layers per packet (no
  third "dark" layer); the echo is the **first (``fh``) layer, an array of
  little-endian uint16 samples** (the high byte is the smooth echo MSB, the low
  byte its LSB -- a per-byte view looks like a decaying "odd" stream interleaved
  with a uniform "even" one).  Use :func:`echo_layer` and publish ``UINT16``.
  Taking the last layer here (the GCV-10 rule) yields a washed-out low-res/AGC
  display layer -- the original "looks wrong in rqt" bug.

Neither a frequency nor a usable per-ping timestamp is carried in the imagery
sub-header, so the driver takes frequency from configuration and stamps pings
with their receive time (see node.py).
"""
from collections import namedtuple

EB07 = b'\xeb\x07'
D807 = b'\xd8\x07'
STATUS_MAGIC = b'\x8e\x03'       # GCV status broadcast (239.254.2.2:50050)
# The :50050 stream multiplexes two 34-byte sub-types, discriminated by the
# payload byte at offset 9 (see docs/gcv_protocol.md):
#   0x00 -> settings echo (carries the historical transmit flag at this byte)
#   0xe4 -> a separate mode/status sub-type, broadcast regardless of tx state
# Earlier bench notes read byte 9 as a bare transmit flag (0x00 on / 0x01 off);
# the wet capture shows 0x00 and 0xe4 interleaved regardless of transmit state,
# so byte 9 is (also) a sub-type selector.  We therefore read a transmit state
# only from the 0x00/0x01 sub-types and treat the 0xe4 sub-type as carrying no
# transmit information (rather than flapping it "off").
#
# The 0xe4 sub-type holds a u16 at offset 20 that we briefly took for a nadir
# depth, but cross-checking the 2026-06-10 capture against the M3 multibeam
# showed it is a HELD, coarse value that does NOT track depth (it lags by tens
# of seconds and sits metres off the M3 nadir) -- so its meaning is unconfirmed
# (a candidate mode/status field) and the driver does NOT decode or publish it.
# A real nadir depth, if wanted, comes from bottom-tracking the down-look
# imagery in a downstream node, not from this field.
STATUS_SUBTYPE_OFFSET = 9
STATUS_TX_OFFSET = STATUS_SUBTYPE_OFFSET     # legacy alias (tx flag == sub-type byte)
STATUS_SUBTYPE_SETTINGS = 0x00


def status_subtype(payload):
    """
    Return the ``8e03`` status sub-type byte (offset 9), or None if not status.

    See the module constants for the meaning of each sub-type.
    """
    if payload[:2] != STATUS_MAGIC or len(payload) <= STATUS_SUBTYPE_OFFSET:
        return None
    return payload[STATUS_SUBTYPE_OFFSET]


def status_transmitting(payload):
    """
    Return transmit state from a GCV ``8e03`` status frame, or None if unknown.

    Reads the transmit flag only from the settings sub-type: ``0x00`` ->
    transmitting, ``0x01`` -> off.  Returns None for a non-status payload and
    for the ``0xe4`` sub-type (broadcast regardless of transmit state, so
    reading it as "off" would flap the flag).
    """
    sub = status_subtype(payload)
    if sub == STATUS_SUBTYPE_SETTINGS:
        return True
    if sub == 0x01:
        return False
    return None


# Render-layer header signatures (little-endian sample pairs).
FH = bytes([218, 4, 216, 4])    # da 04 d8 04  full-packet first-layer header
FHS = bytes([242, 3, 240, 3])   # f2 03 f0 03  short-packet first-layer header
SH = bytes([174, 2, 172, 2])    # ae 02 ac 02  full-packet later-layer header
SHS = bytes([250, 1, 248, 1])   # fa 01 f8 01  short-packet later-layer header

# Each GCV-20 first-layer sample block ends with a fixed trailer record the
# device appends before the next layer header (side-scan) or end of packet
# (down-look):  ``43 <id> <opener> | <op>[seq] | 52 80 10 | 5b <crc…>``.  The
# sample length is *delimited* by this trailer, not length-prefixed -- the eb07
# LE-length at offset 4 covers the whole payload, and the FH/SH headers are
# fixed magic (identical on every packet regardless of size), so neither yields
# the sample-region length.  We locate the trailer by its fixed inner magic and
# cut the samples at the ``0x43`` record opener, rather than assume a fixed
# sample count (which varies with range / firmware / generation).  Left
# undecoded, the trailer reads back as constant bright lines at every
# packet-concatenation boundary in the waterfall (issue #26).
#
# The bytes between the ``0x43`` opener and the ``52 80 10`` magic vary by
# firmware (an early bench capture had ``96 03``; the 2026-06-10 GCV-20 wet
# capture has ``e6 24``) and the trailer appears on BOTH the down-look and
# side-scan first layers.  So we anchor only on the two invariants -- the
# ``0x43`` opener and the ``52 80 10`` magic ~6 bytes later -- not the variable
# middle.  Keying on ``96 03`` alone (the old rule) left the trailer in place on
# this firmware, which is exactly the residual banding seen in the waterfall.
TRAILER_MAGIC = b'\x52\x80\x10'      # fixed inner magic of the trailer record
TRAILER_OPENER_BYTE = 0x43           # '43 <id>…' record-opener byte
TRAILER_OPENER_SPAN = 10             # opener sits within this many bytes before the magic
TRAILER_TAIL_WINDOW = 24             # trailer sits within this many bytes of the end
# The first packet of a scan line begins with a per-ping header: one 16-bit
# value repeated for a device-chosen run before the samples (reads back as the
# bright near-range band).  Detected by value-repeat (length not hard-coded) and
# dropped only when the run is longer than real echo could plausibly produce.
LEADING_RUN_MIN_BYTES = 24           # >=12 repeated uint16 samples

CHANNEL_OFFSET = 12
# Render-layer byte at offset 8 also encodes the beam TYPE: the
# down-look ("water column") beam is 0x0d on both generations, while side-scan
# (sidescan) is 0x0e (GCV-20) / 0x0f (GCV-10). So the down-look stream is
# identifiable intrinsically, independent of channel number or packet size.
LAYER_OFFSET = 8
WATER_COLUMN_LAYER = 0x0d
# Sub-header value-width tag at payload offset 13 distinguishes the device
# generation: 0x11 (GCV-10, 1-byte value) vs 0x12 (GCV-20, 2-byte value).
# Verified 100% consistent across both captures, every channel -- a positive,
# size-independent signal on every packet (unlike packet-size heuristics).
GEN_TAG_OFFSET = 13
GEN_BY_TAG = {0x11: 'gcv10', 0x12: 'gcv20'}
MIN_DATA_LEN = 32               # below this an eb07 payload has no sample data
# A real scan line is ~2048 bins; cap the accumulator so a degenerate stream
# (one channel forever, no markers) can't grow it without bound.
MAX_SCAN_BYTES = 65536


def dark_layer(payload):
    """
    Return the dark (high-resolution) render layer from one eb07 payload (GCV-10).

    The dark layer is everything after the final layer-header signature plus
    its 4 signature bytes.  Returns an empty bytes object if no signature is
    present.
    """
    i = payload.rfind(SH)
    if i < 0:
        i = payload.rfind(SHS)
    if i < 0:
        return b''
    return payload[i + 4:]


def strip_first_layer_trailer(layer):
    """
    Drop the per-packet trailer record the GCV-20 appends after the echo samples.

    The trailer is delimited, not length-prefixed: ``43 <id> … 52 80 10 …``.
    Find its fixed magic near the end, then the ``0x43`` record opener within a
    few bytes before it (so sample data that coincidentally contains ``52 80 10``
    elsewhere can't trigger a cut), and return the bytes before the opener.
    Returns ``layer`` unchanged when no trailer is present (older captures,
    synthetic packets), so the delimiter -- not a hard-coded length -- bounds the
    sample region.
    """
    m = layer.rfind(TRAILER_MAGIC)
    if m < 0 or m < len(layer) - TRAILER_TAIL_WINDOW:
        return layer
    # The record opens with 0x43 a handful of bytes before the magic (the bytes
    # between vary by firmware -- see the module comment). The opener is the
    # rightmost 0x43 in that span; an earlier coincidental 0x43 in real samples
    # is left intact.
    opener = layer.rfind(TRAILER_OPENER_BYTE, max(0, m - TRAILER_OPENER_SPAN), m)
    if opener < 0:
        return layer
    return layer[:opener]


def strip_leading_ping_header(block):
    """
    Drop the per-ping header that opens the first packet of a GCV-20 scan line.

    The header is one 16-bit value repeated for a device-chosen run before the
    samples begin.  Detected by value-repeat (the run length is not hard-coded)
    and removed only when the run is long enough that real echo could not produce
    it (:data:`LEADING_RUN_MIN_BYTES`).  Short coincidental repeats in genuine
    samples are left intact.
    """
    if len(block) < LEADING_RUN_MIN_BYTES:
        return block
    pat = block[:2]
    n = 0
    while n + 2 <= len(block) and block[n:n + 2] == pat:
        n += 2
    return block[n:] if n >= LEADING_RUN_MIN_BYTES else block


def echo_layer(payload):
    """
    Return the GCV-20 16-bit echo from one eb07 payload as raw uint16-LE bytes.

    The first (``fh``/``fhs``) render layer -- the bytes from the first-layer
    header + 4 up to the next ``sh``/``shs`` (or end of packet if absent, as on
    down-look) -- is an array of little-endian uint16 samples: the smooth high
    byte is the echo MSB, the noisy low byte its LSB (so a per-byte view shows a
    decaying odd stream interleaved with a uniform even stream).  Returned as-is
    so the caller can publish ``DTYPE_UINT16``; trimmed to a whole number of
    samples so the per-packet concatenation can't straddle a sample across the
    packet boundary (the layer length varies, sometimes odd).  Taking only the
    high byte would be a correct but 8-bit-truncated view.  Returns ``b''`` if
    no first-layer header is present.
    """
    # Search past the fixed prefix/sub-header so a coincidental signature byte
    # pattern in the eb07 magic / length / sub-header can't shift the start.
    f = payload.find(FH, CHANNEL_OFFSET)
    if f < 0:
        f = payload.find(FHS, CHANNEL_OFFSET)
    if f < 0:
        return b''
    # The first layer ends at the next layer header of EITHER form -- a full
    # first layer can be followed by a short later header (and vice versa), so
    # don't couple the terminator to the opener type.
    ends = [p for p in (payload.find(SH, f + 4), payload.find(SHS, f + 4)) if p >= 0]
    s = min(ends) if ends else len(payload)
    first = strip_first_layer_trailer(payload[f + 4:s])
    return first[:len(first) // 2 * 2]


def is_water_column(payload):
    """
    Return True if this imagery payload is the down-look beam.

    Identified by the render-layer byte at ``LAYER_OFFSET`` being
    ``WATER_COLUMN_LAYER`` (0x0d) -- the same on both generations, so it tells the
    down-look stream from the side-scan streams without relying on the channel
    number or packet size.  Does not distinguish port from starboard (both
    side-scan sides share the non-down-look layer byte); use the channel map for
    that.
    """
    return len(payload) > LAYER_OFFSET and payload[LAYER_OFFSET] == WATER_COLUMN_LAYER


# eb07 sub-header range fields (see docs/gcv_protocol.md).
RANGE_BRACKET_OFFSET = 13         # coarse range-bracket index (shared by all channels)
RANGE_VARINT_OFFSET = 14          # down-look per-ping bottom-range LEB128 varint
RANGE_UNIT_M = 0.0005             # 0.5 mm units (matches the TCP range command)


def decode_leb128(buf, i):
    """
    Decode an unsigned LEB128 varint at ``buf[i:]``.

    Returns ``(value, next_index)``, or ``(None, i)`` if the buffer ends before
    the varint terminates.
    """
    value = shift = 0
    while i < len(buf):
        b = buf[i]
        value |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            return value, i
        shift += 7
    return None, i


def subheader_bottom_range_m(payload):
    """
    Return the down-look per-ping bottom range (metres) from an eb07 sub-header.

    The down-look (water-column) sub-header carries the device's measured bottom
    range as an unsigned LEB128 varint at :data:`RANGE_VARINT_OFFSET`, in 0.5 mm
    units (:data:`RANGE_UNIT_M`, the same unit as the TCP range command).
    Validated against the M3 multibeam to ~1% (see ``docs/gcv_protocol.md``).
    Returns None for a non-eb07 or non-down-look payload, or a short/garbled
    sub-header.
    """
    if payload[:2] != EB07 or not is_water_column(payload):
        return None
    raw, _ = decode_leb128(payload, RANGE_VARINT_OFFSET)
    if raw is None:
        return None
    return raw * RANGE_UNIT_M


# Down-look sub-header: three LEB128 varints (0.5 mm units) with fixed markers --
#   0d 01 03 09 <chan> <bracket> | v1 | 19 00 23 | v2 | 2a | v3 | 31 02 3f | FH…
# (verified on 29k+ packets; see docs/gcv_protocol.md).
DownlookSubheader = namedtuple(
    'DownlookSubheader', 'bracket bottom_range_m display_range_m near_field_m')


def parse_downlook_subheader(payload):
    """
    Parse the full down-look eb07 sub-header, or return None.

    Returns a :class:`DownlookSubheader` with:
      * ``bottom_range_m`` (v1) -- the device's measured bottom range/depth;
      * ``display_range_m`` (v2) -- the auto-ranged scan extent
        (``bin_size = display_range_m / n_bins``);
      * ``near_field_m`` (v3) -- a small near-field/start term (~0.1 m, meaning
        unconfirmed).
    Validates the fixed markers (``19 00 23`` / ``2a`` / opening ``0d 01 03 09``)
    so a garbled or non-down-look payload yields None.
    """
    if (payload[:2] != EB07 or not is_water_column(payload) or len(payload) < 33
            or payload[8:12] != b'\x0d\x01\x03\x09'):
        return None
    v1, o = decode_leb128(payload, RANGE_VARINT_OFFSET)
    if v1 is None or payload[o:o + 3] != b'\x19\x00\x23':
        return None
    v2, o = decode_leb128(payload, o + 3)
    if v2 is None or o >= len(payload) or payload[o] != 0x2a:
        return None
    v3, _ = decode_leb128(payload, o + 1)
    if v3 is None:
        return None
    return DownlookSubheader(payload[RANGE_BRACKET_OFFSET],
                             v1 * RANGE_UNIT_M, v2 * RANGE_UNIT_M, v3 * RANGE_UNIT_M)


class PingAssembler:
    """
    Reassemble GCV imagery datagrams into per-channel scan lines.

    Feed each UDP payload to :meth:`feed`; it returns a list of completed
    ``(channel, samples, stamp)`` tuples (usually empty, occasionally one when
    a channel's run ends).  ``stamp`` is the ``recv_time`` passed with the
    first packet of that run, so callers can timestamp a scan line by the
    arrival of its first packet.  Call :meth:`flush` at end of stream to emit
    any trailing accumulation.
    """

    def __init__(self, extractor=dark_layer):
        # Per-packet sample extractor, chosen by device generation:
        # dark_layer (GCV-10) or echo_layer (GCV-20).
        self._extract = extractor
        self._cur_ch = None
        self._acc = bytearray()
        self._t0 = 0.0

    def _emit(self, out):
        if self._cur_ch is not None and self._acc:
            out.append((self._cur_ch, bytes(self._acc), self._t0))
        self._acc = bytearray()

    def feed(self, payload, recv_time=0.0):
        """Consume one datagram payload; return any completed scan lines."""
        out = []
        if payload[:2] == D807:
            self._emit(out)
            self._cur_ch = None
        elif payload[:2] == EB07 and len(payload) > MIN_DATA_LEN:
            ch = payload[CHANNEL_OFFSET]
            block = self._extract(payload)
            if ch != self._cur_ch:
                self._emit(out)
                self._cur_ch = ch
                self._t0 = recv_time
                # The per-ping leading header rides only the first packet of a
                # scan line, and only on the GCV-20 (echo_layer) stream.
                if self._extract is echo_layer:
                    block = strip_leading_ping_header(block)
            self._acc.extend(block)
            if len(self._acc) > MAX_SCAN_BYTES:
                # Degenerate stream (no channel change, no marker): drop the
                # runaway accumulation rather than grow without bound.
                self._acc = bytearray()
                self._cur_ch = None
        return out

    def flush(self):
        """Emit any accumulated scan line at end of stream."""
        out = []
        self._emit(out)
        self._cur_ch = None
        return out
