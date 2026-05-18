"""
ZDA serial bridge node.

Subscribes to an SBG ``SbgUtcTime`` topic and emits NMEA ``$ZDA`` time/date
sentences to a serial port at the topic's rate (typically 1 Hz). Output is
suppressed until the SBG reports a valid UTC time so the M3 sonar (or any
other downstream sounder) never receives a wrong wall-clock during cold-start.
"""

import threading
from typing import Optional

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sbg_driver.msg import SbgUtcTime
import serial


def validate_talker_id(raw: str) -> str:
    """
    Validate and normalise a NMEA talker ID.

    ``isalpha()`` accepts non-ASCII letters (e.g. ``'ßZ'``), and
    ``.upper()`` on some of them expands length (``'ß'`` → ``'SS'``),
    which would survive a permissive gate but raise UnicodeEncodeError
    later when the sentence is encoded to ASCII. Restrict to plain
    ASCII NMEA talker characters so the failure is loud and early.

    Returns the upper-cased two-character ID, or raises ``ValueError``.
    """
    if (len(raw) != 2 or not raw.isascii() or not raw.isalpha()):
        raise ValueError(
            f'talker_id must be 2 ASCII alphabetic chars, got {raw!r}')
    return raw.upper()


def validate_timing_params(
    stale_age_warn_sec: float,
    stale_age_error_sec: float,
    startup_grace_sec: float,
    reconnect_delay_sec: float,
) -> None:
    """
    Validate the four timing parameters at node construction.

    Raises ``ValueError`` if any value is negative, or if
    ``stale_age_warn_sec > stale_age_error_sec`` (which would make the
    WARN branch of the diagnostic ladder unreachable — the ERROR branch
    catches first — producing a silently degraded diagnostic ladder
    that's hard to spot in the field).
    """
    for name, value in (
            ('stale_age_warn_sec', stale_age_warn_sec),
            ('stale_age_error_sec', stale_age_error_sec),
            ('startup_grace_sec', startup_grace_sec),
            ('reconnect_delay_sec', reconnect_delay_sec)):
        if value < 0:
            raise ValueError(f'{name} must be >= 0, got {value}')
    if stale_age_warn_sec > stale_age_error_sec:
        raise ValueError(
            f'stale_age_warn_sec ({stale_age_warn_sec}) must be <= '
            f'stale_age_error_sec ({stale_age_error_sec}); otherwise '
            f'the WARN branch of the diagnostic ladder is unreachable.')


def format_zda(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    nanosec: int,
    talker_id: str = 'GP',
) -> str:
    r"""
    Format an NMEA ``$xxZDA`` time/date sentence.

    Returns the full sentence including leading ``$``, trailing ``*HH\r\n``,
    and an XOR checksum computed over the body. Local-zone fields are left
    empty so the time is interpreted as UTC.
    """
    # Truncate the fraction to centiseconds rather than rounding via
    # ``%05.2f`` on ``second + nanosec/1e9``. Rounding would carry 99.5 cs
    # into the integer second (e.g. ``sec=59, nanosec=999_999_999`` →
    # ``60.00``) without carrying into minute/hour/day, producing invalid
    # ``$ZDA`` sentences across boundaries. Truncation preserves the
    # integer second as published (leap-second sec=60 still emits ``60.00``).
    cs = min(99, nanosec // 10_000_000)
    body = (f'{talker_id}ZDA,'
            f'{hour:02d}{minute:02d}{second:02d}.{cs:02d},'
            f'{day:02d},{month:02d},{year:04d},,')
    chk = 0
    for c in body:
        chk ^= ord(c)
    return f'${body}*{chk:02X}\r\n'


class ZdaSerialBridgeNode(Node):
    """ROS 2 node bridging SBG UTC time to NMEA $ZDA on a serial port."""

    def __init__(self) -> None:
        super().__init__('zda_serial_bridge')

        self.declare_parameter('device', '/dev/ttyS2')
        self.declare_parameter('baud', 9600)
        self.declare_parameter('talker_id', 'GP')
        # SbgUtcTimeStatus.clock_utc_status:
        #   0 unknown, 1 UTC initialized but leap seconds NOT yet known,
        #   2 UTC fully valid (leap-second almanac downloaded).
        # Default 2: status 1 emits GPS time mislabeled as UTC and is wrong
        # by the GPS-UTC offset (~18 s) until the SBG receives the
        # leap-second almanac. For sonar time-tagging that is a data
        # integrity issue, not a cosmetic one. Drop to 1 only if a
        # downstream consumer prefers degraded time over no time.
        self.declare_parameter('min_utc_status', 2)
        # Optional stricter gate: also require PPS sync.
        self.declare_parameter('require_utc_sync', False)
        self.declare_parameter('reconnect_delay_sec', 2.0)
        # 1 Hz topic, so anything past a couple of seconds is a real gap.
        self.declare_parameter('stale_age_warn_sec', 2.5)
        self.declare_parameter('stale_age_error_sec', 10.0)
        # Cold-start grace: during this window after node startup, the
        # absence of any SbgUtcTime message is reported as "starting up"
        # (OK), not "no data ever" (ERROR). Without this, the diagnostic
        # alarms ERROR on the very first tick because the SBG driver
        # hasn't had a chance to publish its first sample yet.
        self.declare_parameter('startup_grace_sec', 5.0)

        self._device = self.get_parameter('device').value
        self._baud = int(self.get_parameter('baud').value)
        self._talker_id = self.get_parameter('talker_id').value
        self._min_utc_status = int(self.get_parameter('min_utc_status').value)
        self._require_utc_sync = bool(
            self.get_parameter('require_utc_sync').value)
        self._reconnect_delay = float(
            self.get_parameter('reconnect_delay_sec').value)
        self._stale_warn = float(self.get_parameter('stale_age_warn_sec').value)
        self._stale_error = float(
            self.get_parameter('stale_age_error_sec').value)
        self._startup_grace = float(
            self.get_parameter('startup_grace_sec').value)
        self._node_start_ns = self.get_clock().now().nanoseconds

        self._talker_id = validate_talker_id(self._talker_id)
        validate_timing_params(
            self._stale_warn, self._stale_error,
            self._startup_grace, self._reconnect_delay)

        self._lock = threading.Lock()
        self._serial: Optional[serial.Serial] = None
        self._last_open_attempt_ns = 0
        self._write_count = 0
        self._suppressed_count = 0
        self._serial_error_count = 0
        self._last_emit_ns: Optional[int] = None
        self._last_msg_ns: Optional[int] = None
        self._last_status_text = 'startup'
        # Explicit, machine-readable gate state. Drives the diagnostic
        # level for the "messages arriving but no emit yet" branch so
        # the logic doesn't depend on parsing the human-readable
        # _last_status_text. Values:
        #   None              — no SbgUtcTime received yet
        #   'suppressed_status' — clock_utc_status < min_utc_status
        #   'suppressed_sync' — require_utc_sync=True and not synced
        #   'open'            — gate is open (emit attempted; may have
        #                       failed at the transport layer, but that
        #                       is a separate ERROR class).
        self._gate_state: Optional[str] = None
        self._last_clock_utc_status = -1
        self._last_clock_utc_sync = False

        self._open_serial()

        sub_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        self._sub = self.create_subscription(
            SbgUtcTime, 'utc_time', self._on_utc_time, sub_qos)

        self._diag_pub = self.create_publisher(
            DiagnosticArray, '/diagnostics', 10)
        self._diag_timer = self.create_timer(1.0, self._publish_diagnostics)

        self.get_logger().info(
            f'zda_serial_bridge started: device={self._device} '
            f'baud={self._baud} talker_id={self._talker_id} '
            f'min_utc_status={self._min_utc_status} '
            f'require_utc_sync={self._require_utc_sync}')

    def _open_serial(self) -> None:
        # The rate-limit check + last-attempt update happen under the
        # lock; the blocking ``serial.Serial(...)`` call deliberately
        # does not. A hanging open on a wedged USB-serial device must
        # not stall the diagnostic publisher or the ``_on_utc_time``
        # callback, both of which can also acquire ``self._lock``.
        with self._lock:
            if self._serial is not None:
                return
            now_ns = self.get_clock().now().nanoseconds
            if (self._last_open_attempt_ns
                    and (now_ns - self._last_open_attempt_ns) / 1e9
                    < self._reconnect_delay):
                return
            self._last_open_attempt_ns = now_ns

        try:
            new_serial = serial.Serial(
                self._device, self._baud, timeout=1.0, write_timeout=1.0)
        except (serial.SerialException, OSError) as exc:
            with self._lock:
                self._serial_error_count += 1
            # Throttle the per-attempt error log: when the device is
            # permanently absent, the 1 Hz diagnostic tick (rate-limited
            # to ``reconnect_delay_sec``) would otherwise flood rosout
            # with identical lines over a long deployment. The ERROR
            # diagnostic and ``_serial_error_count`` KeyValue continue
            # to surface the state on every tick.
            self.get_logger().error(
                f'Serial open {self._device} failed: {exc}; '
                f'retry in {self._reconnect_delay:.1f}s',
                throttle_duration_sec=30.0)
            return

        with self._lock:
            # Re-check under the lock: under MultiThreadedExecutor
            # another callback may have raced through _open_serial
            # while we were blocked in serial.Serial(...). The
            # rate-limit normally prevents this (the second caller
            # bails on _last_open_attempt_ns), but a _close_serial
            # call interleaved with our open would also land here.
            # Drop the freshly opened port rather than overwrite the
            # other thread's state, so the FD doesn't leak.
            if self._serial is not None:
                try:
                    new_serial.close()
                except (serial.SerialException, OSError):
                    pass
                return
            self._serial = new_serial
        self.get_logger().info(
            f'Opened serial {self._device} @ {self._baud}')

    def _close_serial(self) -> None:
        # Same pattern as _open_serial: detach the port reference under
        # the lock, then call the potentially-blocking .close() outside.
        with self._lock:
            port = self._serial
            self._serial = None
        if port is None:
            return
        try:
            port.close()
        except (serial.SerialException, OSError):
            pass

    def _on_utc_time(self, msg: SbgUtcTime) -> None:
        now_ns = self.get_clock().now().nanoseconds
        clock_utc_status = int(msg.clock_status.clock_utc_status)
        clock_utc_sync = bool(msg.clock_status.clock_utc_sync)

        # Gate evaluation + state updates happen under the lock so the
        # diagnostic snapshot sees a consistent view. The blocking
        # serial write below is deliberately outside the lock.
        with self._lock:
            self._last_msg_ns = now_ns
            self._last_clock_utc_status = clock_utc_status
            self._last_clock_utc_sync = clock_utc_sync

            if clock_utc_status < self._min_utc_status:
                self._suppressed_count += 1
                self._gate_state = 'suppressed_status'
                self._last_status_text = (
                    f'suppressed: clock_utc_status={clock_utc_status} '
                    f'< {self._min_utc_status}')
                return
            if self._require_utc_sync and not clock_utc_sync:
                self._suppressed_count += 1
                self._gate_state = 'suppressed_sync'
                self._last_status_text = 'suppressed: clock_utc_sync=False'
                return
            # Gate is open: clock_utc_status meets the minimum and (if
            # required) sync is asserted. Mark even before the write
            # attempt — a transport failure further down is reported
            # via _serial_error_count / level=ERROR, not by reverting
            # the gate.
            self._gate_state = 'open'
            serial_ref = self._serial

        sentence = format_zda(
            int(msg.year), int(msg.month), int(msg.day),
            int(msg.hour), int(msg.min), int(msg.sec),
            int(msg.nanosec), talker_id=self._talker_id,
        )
        payload = sentence.encode('ascii')

        if serial_ref is None:
            self._open_serial()
            with self._lock:
                serial_ref = self._serial
        if serial_ref is None:
            with self._lock:
                self._last_status_text = f'serial closed ({self._device})'
            return

        # Blocking write outside the lock. ``write_timeout=1.0`` on a
        # stalled port could otherwise block the diagnostic publisher
        # for a full second every cycle.
        try:
            serial_ref.write(payload)
        except (serial.SerialException, OSError) as exc:
            with self._lock:
                self._serial_error_count += 1
                self._last_status_text = f'write error: {exc}'
            self.get_logger().error(
                f'Serial write {self._device} failed: {exc}; reopening')
            self._close_serial()
            return

        with self._lock:
            self._write_count += 1
            self._last_emit_ns = now_ns
            self._last_status_text = 'OK'

    def _publish_diagnostics(self) -> None:
        # Reconnect on every diagnostic tick so port recovery doesn't
        # depend on continued SbgUtcTime flow. _open_serial is itself
        # idempotent (returns immediately if already open) and
        # rate-limited by _reconnect_delay, so a 1 Hz unconditional
        # call here only actually re-opens at most every
        # `reconnect_delay_sec`. Avoiding the bare ``self._serial is
        # None`` pre-check keeps every read of ``self._serial`` under
        # the lock.
        self._open_serial()

        # Snapshot state under the lock. Every writer in
        # _on_utc_time / _open_serial / _close_serial brackets its
        # state updates with ``with self._lock:`` and the blocking
        # serial I/O happens outside the lock, so this snapshot
        # observes a consistent view of (serial_open, last_emit_ns,
        # last_msg_ns, gate_state, counters) even under
        # MultiThreadedExecutor.
        with self._lock:
            serial_open = self._serial is not None
            last_emit_ns = self._last_emit_ns
            last_msg_ns = self._last_msg_ns
            last_status_text = self._last_status_text
            gate_state = self._gate_state
            write_count = self._write_count
            suppressed_count = self._suppressed_count
            serial_error_count = self._serial_error_count
            last_clock_utc_status = self._last_clock_utc_status
            last_clock_utc_sync = self._last_clock_utc_sync

        now_ns = self.get_clock().now().nanoseconds

        last_emit_age = (
            None if last_emit_ns is None
            else (now_ns - last_emit_ns) / 1e9
        )
        last_msg_age = (
            None if last_msg_ns is None
            else (now_ns - last_msg_ns) / 1e9
        )

        # Distinguish three cold-start scenarios that the previous logic
        # collapsed into a single ERROR:
        #   1. Node just started, SBG hasn't published yet — OK (warmup).
        #   2. SbgUtcTime arriving but emission gated by min_utc_status /
        #      require_utc_sync — OK (intentional, not a transport bug).
        #   3. Truly stale (had data, gone silent past threshold) — ERROR.
        node_age = (now_ns - self._node_start_ns) / 1e9
        in_startup_grace = node_age < self._startup_grace

        if not serial_open:
            level = DiagnosticStatus.ERROR
            msg_text = f'Serial not connected ({self._device})'
        elif last_msg_age is None:
            # No SbgUtcTime received yet — warmup vs real silence.
            if in_startup_grace:
                level = DiagnosticStatus.OK
                msg_text = (f'starting up ({node_age:.1f}s); '
                            'no SbgUtcTime yet')
            else:
                level = DiagnosticStatus.ERROR
                msg_text = (f'No SbgUtcTime received '
                            f'({node_age:.0f}s after startup)')
        elif last_msg_age > self._stale_error:
            level = DiagnosticStatus.ERROR
            msg_text = f'No SbgUtcTime for {last_msg_age:.1f}s'
        elif last_emit_ns is None:
            # Messages are arriving but no emit has happened yet.
            # gate_state is the source of truth (not a string-match on
            # last_status_text). 'suppressed_*' is intentional gating
            # (OK); anything else is transitional warmup.
            if gate_state in ('suppressed_status', 'suppressed_sync'):
                level = DiagnosticStatus.OK
                msg_text = f'output gated: {last_status_text}'
            else:
                level = DiagnosticStatus.WARN
                msg_text = (f'no ZDA emitted yet '
                            f'(last status: {last_status_text})')
        elif gate_state in ('suppressed_status', 'suppressed_sync'):
            # Re-gated after previously emitting: clock_utc_status
            # dropped back below min, or PPS sync was lost
            # mid-mission. The bridge is working as designed, but
            # downstream consumers have stopped receiving ZDA — that
            # is operationally relevant. Surface WARN for short
            # outages, ERROR once the re-gating persists past
            # stale_age_error (matches the transport-error escalation
            # on the next branch).
            if last_emit_age > self._stale_error:
                level = DiagnosticStatus.ERROR
                msg_text = (f're-gated for {last_emit_age:.1f}s '
                            f'({last_status_text})')
            else:
                level = DiagnosticStatus.WARN
                msg_text = f're-gated: {last_status_text}'
        elif last_emit_age > self._stale_error:
            level = DiagnosticStatus.ERROR
            msg_text = (f'No ZDA emitted for {last_emit_age:.1f}s '
                        f'(last status: {last_status_text})')
        elif last_msg_age > self._stale_warn:
            level = DiagnosticStatus.WARN
            msg_text = f'SbgUtcTime stale: {last_msg_age:.1f}s'
        elif last_emit_age > self._stale_warn:
            level = DiagnosticStatus.WARN
            msg_text = (f'ZDA stale: {last_emit_age:.1f}s '
                        f'(last status: {last_status_text})')
        else:
            level = DiagnosticStatus.OK
            msg_text = 'OK'

        status = DiagnosticStatus()
        status.level = level
        status.name = 'zda_serial_bridge'
        status.message = msg_text
        status.hardware_id = self._device
        status.values = [
            KeyValue(key='device', value=self._device),
            KeyValue(key='baud', value=str(self._baud)),
            KeyValue(key='talker_id', value=self._talker_id),
            KeyValue(key='write_count', value=str(write_count)),
            KeyValue(key='suppressed_count', value=str(suppressed_count)),
            KeyValue(key='serial_error_count',
                     value=str(serial_error_count)),
            KeyValue(key='last_emit_age_s',
                     value=('never' if last_emit_age is None
                            else f'{last_emit_age:.2f}')),
            KeyValue(key='last_msg_age_s',
                     value=('never' if last_msg_age is None
                            else f'{last_msg_age:.2f}')),
            KeyValue(key='clock_utc_status',
                     value=str(last_clock_utc_status)),
            KeyValue(key='clock_utc_sync', value=str(last_clock_utc_sync)),
            KeyValue(key='gate_state',
                     value=str(gate_state) if gate_state else 'unknown'),
            KeyValue(key='last_status', value=last_status_text),
        ]

        diag_msg = DiagnosticArray()
        diag_msg.header.stamp = self.get_clock().now().to_msg()
        diag_msg.status = [status]
        self._diag_pub.publish(diag_msg)

    def destroy_node(self) -> None:
        """Close the serial port before shutdown."""
        self._close_serial()
        super().destroy_node()


def main(args=None) -> None:
    """Entry point: spin the bridge node until interrupted."""
    rclpy.init(args=args)
    node = ZdaSerialBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
