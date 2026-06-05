"""
Garmin GCV-10/20 sidescan imagery decode primitives.

Transport model (validated 2026-06-05 against real survey data and a live
GCV-20):

  eb07 packet = magic ``eb 07`` ``00 00`` + LE-length(4) + 12-byte sub-header
    + render layers.  Each packet carries three render layers, each prefixed
    by a header signature.  The high-resolution echo is the LAST ("dark")
    layer: the bytes after the final ``sh``/``shs`` signature + 4, to the end
    of the packet (~300 samples per full packet, ~248 from the short final
    packet).
  d807 packet = marker delimiting a channel's run within a ping.
  One channel scan line = 7 consecutive same-channel packets (6 full + 1
    short) = ~2048 range bins.
  Channel byte is at payload offset 12 (GCV-20: 0=port, 1=stbd, 2=ClearVu;
    GCV-10 survey data used 3=port, 1=stbd).

Neither a frequency nor a usable per-ping timestamp is carried in the imagery
sub-header, so the driver takes frequency from configuration and stamps pings
with their receive time (see node.py).
"""

EB07 = b'\xeb\x07'
D807 = b'\xd8\x07'

# Render-layer header signatures (little-endian sample pairs).
SH = bytes([174, 2, 172, 2])    # ae 02 ac 02  full-packet layer header
SHS = bytes([250, 1, 248, 1])   # fa 01 f8 01  short final-packet layer header

CHANNEL_OFFSET = 12
MIN_DATA_LEN = 32               # below this an eb07 payload has no sample data
# A real scan line is ~2048 bins; cap the accumulator so a degenerate stream
# (one channel forever, no markers) can't grow it without bound.
MAX_SCAN_BYTES = 65536


def dark_layer(payload):
    """
    Return the dark (high-resolution) render layer from one eb07 payload.

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

    def __init__(self):
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
            if ch != self._cur_ch:
                self._emit(out)
                self._cur_ch = ch
                self._t0 = recv_time
            self._acc.extend(dark_layer(payload))
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
