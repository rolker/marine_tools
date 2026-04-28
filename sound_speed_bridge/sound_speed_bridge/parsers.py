r"""
Parsers for sound speed sensor serial protocols.

Each parser consumes raw serial bytes via feed() and yields zero or more
SoundSpeedReading records. Parsers are registered by name in the PARSERS
dict so the node can instantiate them by parameter.

Sentence framing and quirks (e.g. AML's CRCRLF terminator) live inside the
parser implementations, not in the node.
"""

from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
from typing import Iterable, NamedTuple, Optional


class SoundSpeedReading(NamedTuple):
    """A single parsed reading from a sound speed sensor."""

    sound_speed_m_s: float
    """Speed of sound in m/s. NaN if the sentence framed but did not parse."""

    raw_mm_s: Optional[int]
    """Bit-exact integer mm/s when the parser produced one; None otherwise.

    Preserved through to the Valeport UDP formatter to avoid IEEE-754
    rounding on the m/s -> mm/s conversion.
    """

    raw_bytes: bytes
    """Raw sentence bytes including the original line terminator.

    Used by the passthrough UDP formatter to relay sensor output verbatim.
    Always set, even on parse failure.
    """

    receive_time_ns: int
    """ROS time at which the framing terminator was observed (ns since epoch)."""


class SoundSpeedParser(ABC):
    """Base class for sound speed sensor parsers."""

    @abstractmethod
    def feed(self, data: bytes, receive_time_ns: int) -> Iterable[SoundSpeedReading]:
        """
        Feed raw bytes from serial; yield zero or more readings.

        Implementations buffer partial sentences internally and yield a reading
        for each fully-framed sentence.
        """


class AMLParser(SoundSpeedParser):
    r"""
    Parser for AML SVS sentences.

    The AML SVS emits a decimal m/s value per sentence, terminated by CRCRLF
    (\r\r\n) -- a vendor quirk. Framing on a single \r terminates each line
    cleanly; subsequent \n bytes are skipped as inter-sentence padding.

    Bit-exact m/s -> mm/s conversion uses Decimal arithmetic so the integer
    raw_mm_s field can be relayed downstream without IEEE-754 drift. The
    float sound_speed_m_s field carries IEEE-754 precision for ROS topic
    consumers (which is fine for plotting and downstream calculations).
    """

    _TERMINATOR = b'\r'

    def __init__(self) -> None:
        self._buffer = b''

    def feed(self, data: bytes, receive_time_ns: int) -> Iterable[SoundSpeedReading]:
        """Frame on CR and yield one reading per non-empty sentence."""
        self._buffer += data
        while True:
            idx = self._buffer.find(self._TERMINATOR)
            if idx < 0:
                break
            line = self._buffer[:idx]
            terminator = self._buffer[idx:idx + 1]
            self._buffer = self._buffer[idx + 1:]
            raw = bytes(line) + terminator
            stripped = line.lstrip(b' \t\n').rstrip(b' \t')
            if not stripped:
                continue
            yield self._parse(stripped, raw, receive_time_ns)

    @staticmethod
    def _parse(stripped: bytes, raw: bytes, receive_time_ns: int) -> SoundSpeedReading:
        try:
            decimal_value = Decimal(stripped.decode('ascii'))
        except (UnicodeDecodeError, InvalidOperation):
            return SoundSpeedReading(
                sound_speed_m_s=float('nan'),
                raw_mm_s=None,
                raw_bytes=raw,
                receive_time_ns=receive_time_ns,
            )
        return SoundSpeedReading(
            sound_speed_m_s=float(decimal_value),
            raw_mm_s=int(decimal_value * 1000),
            raw_bytes=raw,
            receive_time_ns=receive_time_ns,
        )


PARSERS = {
    'aml': AMLParser,
}
