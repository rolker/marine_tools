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

        # ``isalpha()`` accepts non-ASCII letters (e.g. ``'ßZ'``), and
        # ``.upper()`` on some of them expands length (``'ß'`` → ``'SS'``),
        # which would survive this gate but raise UnicodeEncodeError later
        # when the sentence is encoded to ASCII. Restrict to plain ASCII
        # NMEA talker characters at startup so the failure is loud and early.
        if (len(self._talker_id) != 2
                or not self._talker_id.isascii()
                or not self._talker_id.isalpha()):
            raise ValueError(
                f'talker_id must be 2 ASCII alphabetic chars, '
                f'got {self._talker_id!r}')
        self._talker_id = self._talker_id.upper()

        self._lock = threading.Lock()
        self._serial: Optional[serial.Serial] = None
        self._last_open_attempt_ns = 0
        self._write_count = 0
        self._suppressed_count = 0
        self._serial_error_count = 0
        self._last_emit_ns: Optional[int] = None
        self._last_msg_ns: Optional[int] = None
        self._last_status_text = 'startup'
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
                self._serial = serial.Serial(
                    self._device, self._baud, timeout=1.0, write_timeout=1.0)
                self.get_logger().info(
                    f'Opened serial {self._device} @ {self._baud}')
            except (serial.SerialException, OSError) as exc:
                self._serial = None
                self._serial_error_count += 1
                self.get_logger().error(
                    f'Serial open {self._device} failed: {exc}; '
                    f'retry in {self._reconnect_delay:.1f}s')

    def _close_serial(self) -> None:
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except (serial.SerialException, OSError):
                    pass
                self._serial = None

    def _on_utc_time(self, msg: SbgUtcTime) -> None:
        now_ns = self.get_clock().now().nanoseconds
        self._last_msg_ns = now_ns
        self._last_clock_utc_status = int(msg.clock_status.clock_utc_status)
        self._last_clock_utc_sync = bool(msg.clock_status.clock_utc_sync)

        if self._last_clock_utc_status < self._min_utc_status:
            self._suppressed_count += 1
            self._last_status_text = (
                f'suppressed: clock_utc_status={self._last_clock_utc_status} '
                f'< {self._min_utc_status}')
            return
        if self._require_utc_sync and not self._last_clock_utc_sync:
            self._suppressed_count += 1
            self._last_status_text = 'suppressed: clock_utc_sync=False'
            return

        sentence = format_zda(
            int(msg.year), int(msg.month), int(msg.day),
            int(msg.hour), int(msg.min), int(msg.sec),
            int(msg.nanosec), talker_id=self._talker_id,
        )
        payload = sentence.encode('ascii')

        if self._serial is None:
            self._open_serial()
        if self._serial is None:
            self._last_status_text = f'serial closed ({self._device})'
            return

        try:
            with self._lock:
                if self._serial is None:
                    return
                self._serial.write(payload)
                self._write_count += 1
                self._last_emit_ns = now_ns
                self._last_status_text = 'OK'
        except (serial.SerialException, OSError) as exc:
            self._serial_error_count += 1
            self._last_status_text = f'write error: {exc}'
            self.get_logger().error(
                f'Serial write {self._device} failed: {exc}; reopening')
            self._close_serial()

    def _publish_diagnostics(self) -> None:
        now_ns = self.get_clock().now().nanoseconds

        last_emit_age = (
            None if self._last_emit_ns is None
            else (now_ns - self._last_emit_ns) / 1e9
        )
        last_msg_age = (
            None if self._last_msg_ns is None
            else (now_ns - self._last_msg_ns) / 1e9
        )

        # Distinguish three cold-start scenarios that the previous logic
        # collapsed into a single ERROR:
        #   1. Node just started, SBG hasn't published yet — OK (warmup).
        #   2. SbgUtcTime arriving but emission gated by min_utc_status /
        #      require_utc_sync — OK (intentional, not a transport bug).
        #   3. Truly stale (had data, gone silent past threshold) — ERROR.
        node_age = (now_ns - self._node_start_ns) / 1e9
        in_startup_grace = node_age < self._startup_grace

        if self._serial is None:
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
        elif self._last_emit_ns is None:
            # Messages are arriving but emission gate is closed.
            # _last_status_text records why: "suppressed: ..." for the
            # intentional UTC-validity gate (OK), anything else is
            # transitional (warming up to first valid UTC).
            if self._last_status_text.startswith('suppressed'):
                level = DiagnosticStatus.OK
                msg_text = f'output gated: {self._last_status_text}'
            else:
                level = DiagnosticStatus.WARN
                msg_text = (f'no ZDA emitted yet '
                            f'(last status: {self._last_status_text})')
        elif last_emit_age > self._stale_error:
            level = DiagnosticStatus.ERROR
            msg_text = (f'No ZDA emitted for {last_emit_age:.1f}s '
                        f'(last status: {self._last_status_text})')
        elif last_msg_age > self._stale_warn:
            level = DiagnosticStatus.WARN
            msg_text = f'SbgUtcTime stale: {last_msg_age:.1f}s'
        elif last_emit_age > self._stale_warn:
            level = DiagnosticStatus.WARN
            msg_text = (f'ZDA stale: {last_emit_age:.1f}s '
                        f'(last status: {self._last_status_text})')
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
            KeyValue(key='write_count', value=str(self._write_count)),
            KeyValue(key='suppressed_count',
                     value=str(self._suppressed_count)),
            KeyValue(key='serial_error_count',
                     value=str(self._serial_error_count)),
            KeyValue(key='last_emit_age_s',
                     value=('never' if last_emit_age is None
                            else f'{last_emit_age:.2f}')),
            KeyValue(key='last_msg_age_s',
                     value=('never' if last_msg_age is None
                            else f'{last_msg_age:.2f}')),
            KeyValue(key='clock_utc_status',
                     value=str(self._last_clock_utc_status)),
            KeyValue(key='clock_utc_sync',
                     value=str(self._last_clock_utc_sync)),
            KeyValue(key='last_status', value=self._last_status_text),
        ]

        diag_msg = DiagnosticArray()
        diag_msg.header.stamp = self.get_clock().now().to_msg()
        diag_msg.status = [status]
        self._diag_pub.publish(diag_msg)

    def destroy_node(self) -> bool:
        """Close the serial port before shutdown."""
        self._close_serial()
        return super().destroy_node()


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
