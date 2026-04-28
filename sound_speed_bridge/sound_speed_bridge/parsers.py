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
import re
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

    temperature_c: Optional[float] = None
    """Optional water temperature in degrees Celsius, when reported by the sensor."""

    pressure_pa: Optional[float] = None
    """Optional pressure in pascals, when reported by the sensor."""


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
    ) -> None:
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
        self._buffer = b''

    def feed(self, data: bytes, receive_time_ns: int) -> Iterable[SoundSpeedReading]:
        """Frame on the configured terminator and yield one reading per non-empty line."""
        self._buffer += data
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
            yield self._parse(stripped, raw, receive_time_ns)

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
# the node so they can read parser-specific parameters; AMLParser has no
# tunables and ignores the argument.
PARSERS = {
    'aml': lambda _node: AMLParser(),
    'regex': lambda node: RegexParser(
        pattern=node.get_parameter('regex_pattern').value,
        sound_speed_scale=node.get_parameter('regex_sound_speed_scale').value,
        line_terminator=node.get_parameter('regex_line_terminator').value,
    ),
}
