"""
Sound speed bridge node.

Reads serial data from a sound-speed sensor, publishes a ROS topic with the
parsed reading, and optionally fans out UDP packets in configurable formats
to downstream consumers. Diagnostics carry the live value plus health
counters so operator UIs see both health and value.
"""

import math
import socket
import threading
from typing import List, Optional

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from marine_interfaces.msg import SoundSpeed
from rcl_interfaces.msg import ParameterDescriptor
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import FluidPressure, Temperature
import serial
from std_msgs.msg import UInt8MultiArray

from .parsers import PARSERS, SoundSpeedParser, SoundSpeedReading
from .sinks import decode_template, FORMATTERS


class _UdpTarget:
    """Internal record describing one configured UDP fan-out destination."""

    __slots__ = (
        'host', 'port', 'format_name',
        'template', 'decoded_template', 'formatter', 'address',
    )

    def __init__(
        self, host, port, format_name, template, decoded_template, formatter,
    ):
        self.host = host
        self.port = port
        self.format_name = format_name
        self.template = template
        self.decoded_template = decoded_template
        self.formatter = formatter
        self.address = (host, port)


class SoundSpeedBridgeNode(Node):
    """ROS 2 node bridging a sound-speed sensor's serial stream to ROS + UDP."""

    _TRIM_WARN_MAX_INTERVAL_S = 300.0
    """Ceiling on the buffer-trim WARN back-off, and the quiet period that resets it."""

    def __init__(self) -> None:
        super().__init__('sound_speed_bridge')

        self.declare_parameter('device', '/dev/ttyS1')
        self.declare_parameter('baud', 9600)
        self.declare_parameter('parser', 'aml')
        self.declare_parameter('frame_id', 'sound_speed_sensor')
        self.declare_parameter('variance', 0.0)
        # Parallel arrays for UDP fan-out. Default single-element empty
        # entries are filtered out at startup.
        self.declare_parameter('udp_hosts', [''])
        self.declare_parameter('udp_ports', [0])
        self.declare_parameter('udp_formats', [''])
        self.declare_parameter('udp_templates', [''])
        self.declare_parameter('reconnect_delay_sec', 2.0)
        self.declare_parameter('valid_sound_speed_min', 1400.0)
        self.declare_parameter('valid_sound_speed_max', 1600.0)
        self.declare_parameter('stale_age_warn_sec', 5.0)
        self.declare_parameter('stale_age_error_sec', 30.0)
        self.declare_parameter('regex_pattern', '')
        self.declare_parameter('regex_sound_speed_scale', 1.0)
        self.declare_parameter('regex_line_terminator', 'cr')
        # Cap on each parser's unframed accumulation buffer. Read once
        # here and handed to the parser factory below, so it is declared
        # read-only: a field `ros2 param set` is then rejected outright
        # rather than reporting success and changing nothing. Changing the
        # cap means restarting the node.
        self.declare_parameter(
            'parser_max_buffer_bytes',
            SoundSpeedParser.DEFAULT_MAX_BUFFER_BYTES,
            ParameterDescriptor(
                read_only=True,
                description=(
                    'Maximum unframed residue the parser buffers, in bytes. '
                    'Must be >= '
                    f'{SoundSpeedParser.MIN_MAX_BUFFER_BYTES} (the serial '
                    'read size and the longest legitimate sentence); an '
                    'out-of-range value fails node startup rather than '
                    'being silently clamped. Static: takes effect at '
                    'construction only.')))

        self._device = self.get_parameter('device').value
        self._baud = self.get_parameter('baud').value
        self._parser_name = self.get_parameter('parser').value
        self._frame_id = self.get_parameter('frame_id').value
        self._variance = self.get_parameter('variance').value
        self._reconnect_delay = self.get_parameter('reconnect_delay_sec').value
        self._valid_min = self.get_parameter('valid_sound_speed_min').value
        self._valid_max = self.get_parameter('valid_sound_speed_max').value
        self._stale_warn = self.get_parameter('stale_age_warn_sec').value
        self._stale_error = self.get_parameter('stale_age_error_sec').value
        self._parser_max_buffer_bytes = self._validated_max_buffer_bytes()

        if self._parser_name not in PARSERS:
            raise ValueError(
                f'Unknown parser {self._parser_name!r}. Known: {list(PARSERS)}')
        self._parser = PARSERS[self._parser_name](self)

        self._udp_targets = self._build_udp_targets()
        self._udp_socket: Optional[socket.socket] = None
        if self._udp_targets:
            self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        topic_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        self._pub = self.create_publisher(SoundSpeed, 'sound_speed', topic_qos)
        self._temp_pub = self.create_publisher(Temperature, 'temperature', topic_qos)
        self._pressure_pub = self.create_publisher(
            FluidPressure, 'fluid_pressure', topic_qos)
        # Per-sentence raw passthrough: the bytes of each *framed* sentence
        # (including its terminator), published even when the sentence fails
        # to parse. This is not a tap on the wire stream — the parser strips
        # inter-sentence padding and drops empty sentences, so concatenating
        # these messages does not byte-exactly reconstruct what arrived on
        # the UART, and a stream that never frames at all (e.g. wrong baud)
        # publishes nothing here. It does capture garbled-but-framed traffic
        # in the bag for post-hoc diagnosis. See rolker/marine_tools#77 for a
        # true byte-stream tap. Bare relative name so it sits beside
        # sound_speed, not under the node name.
        self._raw_pub = self.create_publisher(UInt8MultiArray, 'raw', topic_qos)
        self._diag_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)

        self._lock = threading.Lock()
        self._last_reading: Optional[SoundSpeedReading] = None
        self._last_reading_time_ns: Optional[int] = None
        self._parse_error_count = 0
        self._udp_send_error_count = 0
        # Buffer-trim reporting. The parser owns the counters (plain ints,
        # bumped on the serial thread, read here on the timer thread).
        # Each individual read is atomic under the GIL, but the two are a
        # correlated pair: read separately, the WARN text and the published
        # KeyValues can describe different instants. _publish_diagnostics
        # therefore snapshots both once and uses that snapshot throughout.
        # The node owns the WARN back-off state so a multi-hour framing
        # stall cannot flood the log at the diagnostics rate.
        self._last_buffer_trim_count = 0
        self._last_warned_dropped_bytes = 0
        self._last_trim_warn_ns: Optional[int] = None
        self._last_trim_seen_ns: Optional[int] = None
        self._trim_warn_interval_s = 0.0
        self._serial_reconnect_count = 0
        self._readings_in_window = 0
        self._window_start_ns = self.get_clock().now().nanoseconds
        self._rate_hz = 0.0
        self._serial_connected = False

        self._diag_timer = self.create_timer(1.0, self._publish_diagnostics)

        self._stop_event = threading.Event()
        self._serial_thread = threading.Thread(
            target=self._serial_loop, name='sound_speed_bridge.serial', daemon=True)
        self._serial_thread.start()

        self.get_logger().info(
            f'sound_speed_bridge started: device={self._device} baud={self._baud} '
            f'parser={self._parser_name} '
            f'udp_targets={[(t.host, t.port, t.format_name) for t in self._udp_targets]}')

    def _validated_max_buffer_bytes(self) -> int:
        """
        Read and validate the parser_max_buffer_bytes parameter.

        Validated here as well as in the parser constructor so the failure
        names the *parameter* the operator set, not a constructor argument
        they never see. The floor is the parser's own: a cap below the
        256-byte serial read (or the longest legitimate sentence) would
        shred healthy traffic instead of bounding a stall.
        """
        value = self.get_parameter('parser_max_buffer_bytes').value
        floor = SoundSpeedParser.MIN_MAX_BUFFER_BYTES
        if not isinstance(value, int) or isinstance(value, bool) or value < floor:
            raise ValueError(
                f'parser_max_buffer_bytes must be an integer >= {floor} '
                f'(the serial read size and the longest legitimate '
                f'sentence); got {value!r}')
        return value

    def _warn_on_buffer_trim(self, now_ns: int, trim_count: int, dropped: int) -> None:
        """
        Log a backed-off WARN while the parser is trimming its buffer.

        A framing stall (misconfigured terminator, or UART corruption of the
        framing byte) lasts hours in the field and trims once per serial
        read, so a WARN per diagnostics tick would be ~18k lines -- on top
        of the stale-reading ERROR this same method already emits. The first
        trim warns immediately; the minimum interval then doubles after each
        WARN up to a 5-minute ceiling, and resets once a full ceiling passes
        with **no further trims** -- the quiet period is measured from the
        last trim observed, not from the last WARN, so a stall that is still
        trimming inside the back-off never looks quiet.

        The counters are passed in rather than re-read: the caller snapshots
        the pair once so this WARN and the published KeyValues describe the
        same instant.
        """
        if trim_count == self._last_buffer_trim_count:
            if (self._last_trim_seen_ns is not None
                    and (now_ns - self._last_trim_seen_ns) / 1e9
                    >= self._TRIM_WARN_MAX_INTERVAL_S):
                self._trim_warn_interval_s = 0.0
            return

        self._last_buffer_trim_count = trim_count
        self._last_trim_seen_ns = now_ns
        if self._last_trim_warn_ns is not None:
            elapsed = (now_ns - self._last_trim_warn_ns) / 1e9
            if elapsed < self._trim_warn_interval_s:
                return

        since_last = dropped - self._last_warned_dropped_bytes
        self.get_logger().warning(
            f'Parser buffer overflowed: dropped {since_last} B since the last '
            f'warning ({dropped} B over {trim_count} trims, cap '
            f'{self._parser_max_buffer_bytes} B). Sentences are not framing -- '
            f'check the line terminator and the serial wiring.')
        self._last_warned_dropped_bytes = dropped
        self._last_trim_warn_ns = now_ns
        self._trim_warn_interval_s = min(
            self._trim_warn_interval_s * 2 or 1.0,
            self._TRIM_WARN_MAX_INTERVAL_S)

    def _build_udp_targets(self) -> List[_UdpTarget]:
        hosts_raw = list(self.get_parameter('udp_hosts').value or [])
        ports_raw = list(self.get_parameter('udp_ports').value or [])
        formats_raw = list(self.get_parameter('udp_formats').value or [])
        templates_raw = list(self.get_parameter('udp_templates').value or [])

        if not any(hosts_raw):
            return []

        if len(ports_raw) < len(hosts_raw) or len(formats_raw) < len(hosts_raw):
            raise ValueError(
                f'udp_hosts={hosts_raw} udp_ports={ports_raw} '
                f'udp_formats={formats_raw} '
                'must be parallel arrays of equal length')
        ports = [int(p) for p in ports_raw[:len(hosts_raw)]]
        formats = list(formats_raw[:len(hosts_raw)])
        templates = list(templates_raw[:len(hosts_raw)])
        while len(templates) < len(hosts_raw):
            templates.append('')

        targets: List[_UdpTarget] = []
        for host, port, fmt, template in zip(hosts_raw, ports, formats, templates):
            if not host:
                continue
            if fmt not in FORMATTERS:
                raise ValueError(
                    f'Unknown UDP format {fmt!r} for {host}:{port}. '
                    f'Known: {list(FORMATTERS)}')
            formatter = FORMATTERS[fmt]
            decoded_template = decode_template(fmt, template)
            targets.append(_UdpTarget(
                host, port, fmt, template, decoded_template, formatter))
        return targets

    def _serial_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with serial.Serial(self._device, self._baud, timeout=1.0) as ser:
                    self._serial_connected = True
                    self.get_logger().info(
                        f'Opened serial {self._device} @ {self._baud}')
                    while not self._stop_event.is_set():
                        data = ser.read(256)
                        if not data:
                            continue
                        now_ns = self.get_clock().now().nanoseconds
                        for reading in self._parser.feed(data, now_ns):
                            self._handle_reading(reading)
            except (serial.SerialException, OSError) as exc:
                self._serial_connected = False
                self._serial_reconnect_count += 1
                self.get_logger().error(
                    f'Serial error on {self._device}: {exc}; '
                    f'reconnect in {self._reconnect_delay:.1f}s')
                self._stop_event.wait(self._reconnect_delay)
        self._serial_connected = False

    def _handle_reading(self, reading: SoundSpeedReading) -> None:
        # Shutdown guard: destroy_node()'s join is best-effort (2 s) — a read
        # wedged in the UART layer can outlast it, after which the publishers
        # are destroyed while this daemon thread still runs. Once the stop
        # event is set, publishing is no longer safe.
        if self._stop_event.is_set():
            return
        with self._lock:
            self._last_reading = reading
            self._last_reading_time_ns = self.get_clock().now().nanoseconds
            self._readings_in_window += 1

        if math.isnan(reading.sound_speed_m_s):
            self._parse_error_count += 1

        stamp_sec = reading.receive_time_ns // 1_000_000_000
        stamp_nanosec = reading.receive_time_ns % 1_000_000_000

        msg = SoundSpeed()
        msg.header.stamp.sec = stamp_sec
        msg.header.stamp.nanosec = stamp_nanosec
        msg.header.frame_id = self._frame_id
        msg.sound_speed = float(reading.sound_speed_m_s)
        msg.variance = float(self._variance)
        self._pub.publish(msg)

        # Diagnostic-only publish, deliberately after the primary SoundSpeed
        # publish: _serial_loop catches only (SerialException, OSError), so an
        # unexpected error here must not be able to preempt the primary path.
        self._raw_pub.publish(UInt8MultiArray(data=reading.raw_bytes))

        if reading.temperature_c is not None:
            tmsg = Temperature()
            tmsg.header.stamp.sec = stamp_sec
            tmsg.header.stamp.nanosec = stamp_nanosec
            tmsg.header.frame_id = self._frame_id
            tmsg.temperature = float(reading.temperature_c)
            tmsg.variance = 0.0
            self._temp_pub.publish(tmsg)

        if reading.pressure_pa is not None:
            pmsg = FluidPressure()
            pmsg.header.stamp.sec = stamp_sec
            pmsg.header.stamp.nanosec = stamp_nanosec
            pmsg.header.frame_id = self._frame_id
            pmsg.fluid_pressure = float(reading.pressure_pa)
            pmsg.variance = 0.0
            self._pressure_pub.publish(pmsg)

        ctx = {'frame_id': self._frame_id}
        for target in self._udp_targets:
            payload = target.formatter(reading, target.decoded_template, ctx)
            if payload is None:
                continue
            try:
                self._udp_socket.sendto(payload, target.address)
            except OSError as exc:
                self._udp_send_error_count += 1
                self.get_logger().warning(
                    f'UDP sendto {target.host}:{target.port} failed: {exc}')

    def _publish_diagnostics(self) -> None:
        now_ns = self.get_clock().now().nanoseconds

        with self._lock:
            window_secs = (now_ns - self._window_start_ns) / 1e9
            if window_secs >= 1.0:
                self._rate_hz = self._readings_in_window / window_secs
                self._readings_in_window = 0
                self._window_start_ns = now_ns
            last_reading = self._last_reading
            last_reading_time_ns = self._last_reading_time_ns

        if last_reading_time_ns is None:
            last_age = float('inf')
        else:
            last_age = (now_ns - last_reading_time_ns) / 1e9
        last_value = last_reading.sound_speed_m_s if last_reading else float('nan')

        level = DiagnosticStatus.OK
        msg_text = 'OK'
        if not self._serial_connected:
            level = DiagnosticStatus.ERROR
            msg_text = f'Serial not connected ({self._device})'
        elif last_age > self._stale_error:
            level = DiagnosticStatus.ERROR
            msg_text = f'No reading for {last_age:.1f}s'
        elif last_age > self._stale_warn:
            level = DiagnosticStatus.WARN
            msg_text = f'Reading stale: {last_age:.1f}s old'
        elif math.isnan(last_value):
            level = DiagnosticStatus.WARN
            msg_text = 'Last reading is NaN (parse failed)'
        elif last_value == 0.0:
            level = DiagnosticStatus.WARN
            msg_text = 'Last reading is 0.0 m/s (sensor problem?)'
        elif last_value < self._valid_min or last_value > self._valid_max:
            level = DiagnosticStatus.WARN
            msg_text = (f'Reading {last_value:.3f} m/s outside '
                        f'[{self._valid_min:.1f}, {self._valid_max:.1f}]')

        # One snapshot of the correlated counter pair, used for both the
        # WARN below and the KeyValues published from it.
        dropped_bytes = self._parser.buffer_dropped_bytes
        trim_count = self._parser.buffer_trim_count
        self._warn_on_buffer_trim(now_ns, trim_count, dropped_bytes)

        status = DiagnosticStatus()
        status.level = level
        status.name = 'sound_speed_bridge'
        status.message = msg_text
        status.hardware_id = self._device
        status.values = [
            KeyValue(
                key='sound_speed_m_s',
                value=('nan' if math.isnan(last_value) else f'{last_value:.3f}')),
            KeyValue(key='last_age_s', value=f'{last_age:.2f}'),
            KeyValue(key='parser_rate_hz', value=f'{self._rate_hz:.2f}'),
            KeyValue(key='parse_error_count', value=str(self._parse_error_count)),
            KeyValue(key='buffer_dropped_bytes', value=str(dropped_bytes)),
            KeyValue(key='buffer_trim_count', value=str(trim_count)),
            KeyValue(key='udp_send_error_count',
                     value=str(self._udp_send_error_count)),
            KeyValue(key='serial_reconnect_count',
                     value=str(self._serial_reconnect_count)),
            KeyValue(key='device', value=self._device),
            KeyValue(key='parser', value=self._parser_name),
        ]

        diag_msg = DiagnosticArray()
        diag_msg.header.stamp = self.get_clock().now().to_msg()
        diag_msg.status = [status]
        self._diag_pub.publish(diag_msg)

    def destroy_node(self) -> bool:
        """Stop the serial thread and close the UDP socket before shutdown."""
        self._stop_event.set()
        if self._serial_thread.is_alive():
            self._serial_thread.join(timeout=2.0)
        if self._udp_socket is not None:
            self._udp_socket.close()
        return super().destroy_node()


def main(args=None) -> None:
    """Entry point: spin the bridge node until interrupted."""
    rclpy.init(args=args)
    node = SoundSpeedBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
