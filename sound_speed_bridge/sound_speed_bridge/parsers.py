r"""
Parsers for sound speed sensor serial protocols.

Each parser consumes raw serial bytes via feed() and returns zero or more
SoundSpeedReading records. Parsers are registered by name in the PARSERS
dict so the node can instantiate them by parameter.

Sentence framing and quirks (e.g. AML's CRCRLF terminator) live inside the
parser implementations, not in the node.

Unframed bytes accumulate in a per-parser buffer until the configured
terminator arrives. That buffer is capped (``max_buffer_bytes``): if the
terminator never comes -- a misconfigured regex_line_terminator, or UART
corruption of the framing byte itself -- the buffer would otherwise grow
for the whole run and then emit the entire accumulation as one enormous
"sentence" on the reliable raw topic. See rolker/marine_tools#78.

The cap is on the *unframed residue*, applied after framing, so a read
chunk larger than the cap still frames every complete sentence it
contains. When the residue does exceed the cap the oldest bytes are
dropped (keeping the newest, so framing can resume) and the parser then
discards everything through the next terminator: the surviving residue
starts mid-sentence, and framing it would publish a head-truncated
fragment as if it were a whole sentence -- which for RegexParser can
re.search out a plausible but wrong sound speed. Dropped bytes are not
recoverable here; rolker/marine_tools#77's serial tap is the byte-exact
capture path.
"""

from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable, List, NamedTuple, Optional


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

    temperature_c: Optional[float] = None
    """Optional water temperature in degrees Celsius, when reported by the sensor."""

    pressure_pa: Optional[float] = None
    """Optional pressure in pascals, when reported by the sensor."""


class SoundSpeedParser(ABC):
    r"""
    Base class for sound speed sensor parsers.

    Owns the accumulation buffer and its cap. Subclasses set
    ``self._terminator`` to the byte string they frame on, call
    ``super().__init__()``, and structure ``feed()`` as:

    1. append the new bytes to ``self._buffer``;
    2. call :meth:`_resync` -- if it returns False the parser is still
       throwing away the remains of a trimmed sentence, so call
       :meth:`_trim_residue` and return no readings;
    3. frame and collect readings as usual;
    4. call :meth:`_trim_residue` on whatever is left.

    Trimming *after* framing is what makes the cap mean "maximum unframed
    residue": a serial read larger than the cap still frames every
    complete sentence in it (node.py reads 256 bytes at a time, and the
    cap may legitimately be set that small).

    ``feed()`` is deliberately eager -- it returns a list rather than
    yielding -- so buffer accumulation, trimming and resync happen on the
    call and never depend on the caller exhausting a generator.

    Trim accounting is exposed as two plain attributes, polled by the
    node's diagnostics timer: ``buffer_dropped_bytes`` (the actionable
    magnitude -- how much of the stream was lost) and
    ``buffer_trim_count`` (the event count, which distinguishes one
    overflow from a sustained stall and is the node's edge trigger for
    its backed-off WARN).
    """

    DEFAULT_MAX_BUFFER_BYTES = 4096
    """Default cap on unframed residue.

    16x the node's 256-byte serial read, 16x the longest legitimate
    sentence, and ~5 s of wire at the ~800 B/s observed field rate.
    """

    MIN_MAX_BUFFER_BYTES = 256
    """Floor for ``max_buffer_bytes``.

    256 bytes is both the node's serial read size (node.py: ``ser.read(256)``)
    and the longest legitimate sentence any configured parser frames. Below
    this floor a healthy read chunk would overflow the cap, and a long but
    legitimate sentence could never frame at all -- so a smaller value is
    rejected rather than silently shredding good data.
    """

    _terminator = b'\r'
    """Byte string this parser frames on; subclasses override."""

    def __init__(self, max_buffer_bytes: int = DEFAULT_MAX_BUFFER_BYTES) -> None:
        """Initialize the accumulation buffer and validate the cap."""
        if isinstance(max_buffer_bytes, bool) or not isinstance(max_buffer_bytes, int):
            raise ValueError(
                f'max_buffer_bytes must be an int, got {max_buffer_bytes!r}')
        if max_buffer_bytes < self.MIN_MAX_BUFFER_BYTES:
            raise ValueError(
                f'max_buffer_bytes must be >= {self.MIN_MAX_BUFFER_BYTES} '
                f'(the serial read size and the longest legitimate sentence); '
                f'got {max_buffer_bytes}')
        self._max_buffer_bytes = max_buffer_bytes
        self._buffer = b''
        self._discarding = False
        self.buffer_dropped_bytes = 0
        self.buffer_trim_count = 0

    @abstractmethod
    def feed(self, data: bytes, receive_time_ns: int) -> Iterable[SoundSpeedReading]:
        """
        Feed raw bytes from serial; return zero or more readings.

        Implementations buffer partial sentences internally and produce a
        reading for each fully-framed sentence. The returned sequence is
        fully realized before the call returns.
        """

    def _resync(self) -> bool:
        """
        Drop buffered bytes through the next terminator after a trim.

        Returns True when the buffer is framable -- either no trim is
        pending, or the remains of the trimmed sentence have now been
        discarded. Returns False while still waiting for the terminator
        that ends the broken sentence.

        The residue left by a trim never contains a terminator (framing
        runs first), so this discards exactly the tail of the one damaged
        sentence and never a complete one.
        """
        if not self._discarding:
            return True
        idx = self._buffer.find(self._terminator)
        if idx < 0:
            return False
        self._buffer = self._buffer[idx + len(self._terminator):]
        self._discarding = False
        return True

    def _trim_residue(self) -> None:
        r"""
        Cap the unframed residue, dropping the oldest bytes.

        Keeps the newest ``max_buffer_bytes`` so framing can resume --
        and so a terminator straddling the trim (a retained ``\r`` whose
        ``\n`` arrives in the next chunk) is still recognized. Marks the
        survivor suspect: it begins mid-sentence, so :meth:`_resync`
        discards through the next terminator before framing resumes.
        """
        excess = len(self._buffer) - self._max_buffer_bytes
        if excess <= 0:
            return
        self._buffer = self._buffer[excess:]
        self.buffer_dropped_bytes += excess
        self.buffer_trim_count += 1
        self._discarding = True


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
    _terminator = _TERMINATOR

    def __init__(
        self,
        max_buffer_bytes: int = SoundSpeedParser.DEFAULT_MAX_BUFFER_BYTES,
    ) -> None:
        """Create an AML parser with the given unframed-residue cap."""
        super().__init__(max_buffer_bytes)

    def feed(self, data: bytes, receive_time_ns: int) -> List[SoundSpeedReading]:
        """Frame on CR and return one reading per non-empty sentence."""
        self._buffer += data
        if not self._resync():
            # Still discarding the remains of a trimmed sentence; nothing
            # here can be framed, but the residue still has to stay capped.
            self._trim_residue()
            return []
        readings: List[SoundSpeedReading] = []
        while True:
            # Drop inter-sentence padding (the trailing '\n' of CRCRLF)
            # before framing so raw_bytes for the next sentence does not
            # leak the prior terminator's '\n'. Passthrough sinks rely on
            # raw_bytes being exactly the framed sentence.
            self._buffer = self._buffer.lstrip(b'\n')
            idx = self._buffer.find(self._TERMINATOR)
            if idx < 0:
                break
            line = self._buffer[:idx]
            terminator = self._buffer[idx:idx + 1]
            self._buffer = self._buffer[idx + 1:]
            raw = bytes(line) + terminator
            stripped = line.lstrip(b' \t').rstrip(b' \t')
            if not stripped:
                continue
            readings.append(self._parse(stripped, raw, receive_time_ns))
        # Trim only what is left unframed, so a read chunk larger than the
        # cap still yields every complete sentence it carried.
        self._trim_residue()
        return readings

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


class RegexParser(SoundSpeedParser):
    r"""
    Generic regex-based parser for sensors without a first-class implementation.

    Configured by:

    - pattern: a Python regex with a required named group ``sound_speed`` and
      optional named groups ``temperature`` (degrees Celsius) and
      ``pressure`` (pascals).
    - sound_speed_scale: multiply the captured sound_speed value by this to
      produce m/s. For sensors that emit raw mm/s, set this to 0.001.
    - line_terminator: ``cr`` | ``lf`` | ``crlf`` -- how sentences are framed.

    raw_mm_s is always None for this parser (no contract with the sensor about
    integer representation), so downstream Valeport UDP fan-out rounds from
    the float. Use a first-class parser when bit-exactness matters.
    """

    _TERMINATORS = {'cr': b'\r', 'lf': b'\n', 'crlf': b'\r\n'}

    def __init__(
        self,
        pattern: str,
        sound_speed_scale: float = 1.0,
        line_terminator: str = 'cr',
        max_buffer_bytes: int = SoundSpeedParser.DEFAULT_MAX_BUFFER_BYTES,
    ) -> None:
        """Compile the pattern and validate the terminator and residue cap."""
        super().__init__(max_buffer_bytes)
        if line_terminator not in self._TERMINATORS:
            raise ValueError(
                f'Unknown line_terminator {line_terminator!r}; '
                f'expected one of {list(self._TERMINATORS)}')
        if not pattern:
            raise ValueError('regex_pattern must be non-empty')
        self._regex = re.compile(pattern)
        if 'sound_speed' not in self._regex.groupindex:
            raise ValueError(
                "regex_pattern must contain a named group 'sound_speed'")
        self._terminator = self._TERMINATORS[line_terminator]
        self._scale = float(sound_speed_scale)

    def feed(self, data: bytes, receive_time_ns: int) -> List[SoundSpeedReading]:
        """Frame on the configured terminator; one reading per non-empty line."""
        self._buffer += data
        if not self._resync():
            # Still discarding the remains of a trimmed sentence. Framing it
            # would hand a head-truncated fragment to _parse, whose re.search
            # can match a plausible but wrong value anywhere in the line.
            self._trim_residue()
            return []
        readings: List[SoundSpeedReading] = []
        sep = self._terminator
        sep_len = len(sep)
        while True:
            idx = self._buffer.find(sep)
            if idx < 0:
                break
            line = self._buffer[:idx]
            raw = bytes(line) + sep
            self._buffer = self._buffer[idx + sep_len:]
            stripped = line.strip()
            if not stripped:
                continue
            readings.append(self._parse(stripped, raw, receive_time_ns))
        # Trim only what is left unframed, so a read chunk larger than the
        # cap still yields every complete sentence it carried.
        self._trim_residue()
        return readings

    def _parse(
        self, stripped: bytes, raw: bytes, receive_time_ns: int,
    ) -> SoundSpeedReading:
        try:
            text = stripped.decode('ascii')
        except UnicodeDecodeError:
            return SoundSpeedReading(
                sound_speed_m_s=float('nan'),
                raw_mm_s=None,
                raw_bytes=raw,
                receive_time_ns=receive_time_ns,
            )
        match = self._regex.search(text)
        if match is None:
            return SoundSpeedReading(
                sound_speed_m_s=float('nan'),
                raw_mm_s=None,
                raw_bytes=raw,
                receive_time_ns=receive_time_ns,
            )
        try:
            value = float(match.group('sound_speed')) * self._scale
        except (TypeError, ValueError):
            value = float('nan')
        temperature = self._optional_float(match, 'temperature')
        pressure = self._optional_float(match, 'pressure')
        return SoundSpeedReading(
            sound_speed_m_s=value,
            raw_mm_s=None,
            raw_bytes=raw,
            receive_time_ns=receive_time_ns,
            temperature_c=temperature,
            pressure_pa=pressure,
        )

    @staticmethod
    def _optional_float(match: 're.Match[str]', name: str) -> Optional[float]:
        if name not in match.re.groupindex:
            return None
        captured = match.group(name)
        if captured is None:
            return None
        try:
            return float(captured)
        except (TypeError, ValueError):
            return None


# Mapping of parser name -> factory(node) -> SoundSpeedParser. Factories take
# the node so they can read parser-specific parameters; both parsers read the
# shared parser_max_buffer_bytes cap.
PARSERS = {
    'aml': lambda node: AMLParser(
        max_buffer_bytes=node.get_parameter('parser_max_buffer_bytes').value,
    ),
    'regex': lambda node: RegexParser(
        pattern=node.get_parameter('regex_pattern').value,
        sound_speed_scale=node.get_parameter('regex_sound_speed_scale').value,
        line_terminator=node.get_parameter('regex_line_terminator').value,
        max_buffer_bytes=node.get_parameter('parser_max_buffer_bytes').value,
    ),
}
