"""
Sound speed bridge node.

Reads serial data from a sound-speed sensor, publishes a ROS topic with the
parsed reading, and optionally fans out UDP packets in configurable formats
to downstream consumers. Two byte-level passthrough topics support post-hoc
diagnosis from deployment bags: ``raw`` (per framed sentence) and
``serial_tap`` (the pre-framing wire stream). ``serial_tap`` is off by
default — the topic is not even advertised — and is turned on live, without
a restart, via the ``serial_tap_enabled`` parameter. Diagnostics carry the
live value plus health counters so operator UIs see both health and value.
"""

import math
import socket
import threading
from typing import List, Optional

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from marine_interfaces.msg import SoundSpeed
from rcl_interfaces.msg import ParameterDescriptor, SetParametersResult
import rclpy
from rclpy.exceptions import InvalidHandle
from rclpy.executors import ExternalShutdownException
from rclpy.impl.implementation_singleton import rclpy_implementation as _rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.publisher import Publisher
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
        # Cap on each parser's unframed accumulation buffer. An
        # out-of-range value is fail-loud: the node refuses to start
        # rather than silently clamping, and main() turns that refusal
        # into one FATAL line naming this parameter. Read once here and
        # handed to the parser factory below, so it is declared
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
                    'read size; a floor, not a line-length guarantee). '
                    'Size it well above the longest legitimate sentence of '
                    'the configured protocol: the cap bounds residue after '
                    'framing, so a long line still frames when it and its '
                    'terminator arrive in one read, but residue that '
                    'reaches the cap before a terminator is seen is '
                    'trimmed and discarded -- an undersized cap therefore '
                    'loses whichever sentences straddle a read boundary. '
                    'An out-of-range value fails node startup rather than '
                    'being silently clamped. Static: takes effect at '
                    'construction only.')))

        # The pre-framing wire tap is a diagnostic probe, not a
        # normal-operations topic, so it is OFF by default (operator decision,
        # recorded in .agent/work-plans/issue-77/plan.md): it is turned on only
        # when a wrong-baud / corrupted-framing question is actually being
        # asked, and while off the topic is not advertised at all. rclpy's
        # only descriptor-level dynamism marker is read_only, so
        # read_only=False (stated explicitly rather than left to the default)
        # is what declares this parameter runtime-settable; the
        # _on_set_parameters callback below is what makes a runtime set take
        # effect on the running reader instead of being silently cached.
        self.declare_parameter(
            'serial_tap_enabled', False,
            ParameterDescriptor(
                description=(
                    'Advertise and publish the pre-framing serial_tap byte '
                    'stream. Off by default (diagnostic probe); while off the '
                    'topic does not exist. Settable at runtime: ros2 param '
                    'set /sound_speed_bridge serial_tap_enabled true creates '
                    'the publisher and takes effect on the next serial '
                    'chunk, no restart.'),
                read_only=False))

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

        # Retained: the tap publisher is created on demand (see
        # _set_tap_publishing), so its QoS has to outlive this block.
        self._topic_qos = topic_qos = QoSProfile(
            depth=10, reliability=ReliabilityPolicy.RELIABLE)
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
        # in the bag for post-hoc diagnosis. The companion `serial_tap` topic
        # below covers what this one cannot. Bare relative name so it sits
        # beside sound_speed, not under the node name.
        self._raw_pub = self.create_publisher(UInt8MultiArray, 'raw', topic_qos)
        # Pre-framing wire tap: every non-empty chunk ser.read() returns,
        # published verbatim before any framing or parsing has been applied.
        # Concatenating these messages in publish order reconstructs the UART
        # stream byte-exactly *within one serial connection* — a reconnect
        # (see _serial_loop) drops whatever was in flight and emits no in-band
        # marker, so byte-exactness may only be claimed across a span in which
        # the serial_reconnect_count diagnostic did not change. This is the
        # topic that carries unframeable garbage (wrong baud, bus-voltage sag
        # corrupting the line terminator), the case in which `raw` above is
        # silent. UInt8MultiArray has no header, so bag receive time is the
        # only time base — within one read() interval of wire arrival, which
        # is the resolution the diagnostic question needs. Bare relative name,
        # matching `raw`. See rolker/marine_tools#77.
        #
        # Created on demand, not here: while `serial_tap_enabled` is false the
        # topic is not advertised at all (operator decision, #77 — "I'd rather
        # it only publish if we enable it"), so `ros2 topic list` shows
        # serial_tap exactly when the tap is on and an idle deployment carries
        # no dead endpoint. _set_tap_publishing() creates and destroys it; the
        # serial thread reads the reference through _tap_lock.
        self._tap_pub: Optional[Publisher] = None
        self._tap_lock = threading.Lock()
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
        self._tap_byte_count = 0
        self._tap_error_count = 0
        self._readings_in_window = 0
        self._window_start_ns = self.get_clock().now().nanoseconds
        self._rate_hz = 0.0
        self._serial_connected = False

        self._diag_timer = self.create_timer(1.0, self._publish_diagnostics)

        # Honour a launch-time override (`serial_tap_enabled: true` in a launch
        # file or on the command line): the parameter is read through the same
        # helper the runtime toggle uses, so "enabled at startup" and "enabled
        # later" reach the identical state rather than being two code paths.
        self._set_tap_publishing(
            bool(self.get_parameter('serial_tap_enabled').value))

        # Registered before the serial thread starts, so there is no window in
        # which a set is accepted by the parameter store but not applied to a
        # reader that is already running.
        self.add_on_set_parameters_callback(self._on_set_parameters)

        self._stop_event = threading.Event()
        self._serial_thread = threading.Thread(
            target=self._serial_loop, name='sound_speed_bridge.serial', daemon=True)
        self._serial_thread.start()

        self.get_logger().info(
            f'sound_speed_bridge started: device={self._device} baud={self._baud} '
            f'parser={self._parser_name} '
            f'serial_tap_enabled={self._serial_tap_enabled} '
            f'udp_targets={[(t.host, t.port, t.format_name) for t in self._udp_targets]}')

    def _validated_max_buffer_bytes(self) -> int:
        """
        Read and validate the parser_max_buffer_bytes parameter.

        Validated here as well as in the parser constructor so the failure
        names the *parameter* the operator set, not a constructor argument
        they never see. The floor is the parser's own: 256 B is the serial
        read size, so a cap below it would be overflowed by a single
        healthy read chunk -- shredding good traffic instead of bounding a
        stall. It is a sanity floor, **not** a guarantee that the
        configured protocol's sentences fit: nothing bounds the length of a
        `regex_pattern` line, so the cap must be sized above the longest
        sentence of the protocol in use (AML ~11 B, BizzyBoat `$AML,SVM`
        ~32 B).
        """
        value = self.get_parameter('parser_max_buffer_bytes').value
        floor = SoundSpeedParser.MIN_MAX_BUFFER_BYTES
        if not isinstance(value, int) or isinstance(value, bool) or value < floor:
            raise ValueError(
                f'parser_max_buffer_bytes must be an integer >= {floor} '
                f'(the serial read size -- a sanity floor, not a '
                f'line-length guarantee; size the cap above the longest '
                f'sentence of the configured protocol); got {value!r}')
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

    @property
    def _serial_tap_enabled(self) -> bool:
        """
        Whether the tap is currently advertising and publishing.

        Derived from the publisher rather than tracked in a parallel bool:
        the publisher's existence *is* the enabled state (the topic is not
        advertised while off), and a second flag could only ever disagree
        with it.
        """
        return self._tap_pub is not None

    def _set_tap_publishing(self, enabled: bool) -> None:
        """
        Create or destroy the ``serial_tap`` publisher to match ``enabled``.

        Idempotent, so a repeated set (an operator confirming a state, or a
        launch-time override that already matches) neither re-advertises nor
        tears down a live topic.

        Thread-safety: ``_tap_lock`` guards only the publisher *reference*,
        which is the one piece of state shared between this (executor thread)
        and ``_publish_serial_tap`` (serial thread). It is deliberately its
        own lock and not ``self._lock``: that lock is held on the primary
        reading path (``_handle_reading``) and by the diagnostics timer, so
        reusing it would let a diagnostic contend with the ``SoundSpeed``
        path — the coupling this file's ordering rule exists to prevent. The
        reader copies the reference under the lock and publishes outside it,
        so a publish never runs with a lock held.

        That leaves one deliberate race, and it is already covered: the
        serial thread can be publishing on a reference taken just before a
        disable. ``Publisher.publish`` enters the handle's use-count
        (``Destroyable.__enter__``), which defers the actual destruction
        while a publish is in flight and raises ``InvalidHandle`` — a plain
        ``Exception`` subclass — if the handle is already gone. Either way
        ``_publish_serial_tap``'s broad ``except`` catches it and counts it
        in ``tap_error_count``: at worst a disable costs one counted, logged
        tap error, never a crashed reader thread.
        """
        if enabled:
            # Check-then-act on _tap_pub without holding _tap_lock. That is
            # safe only because this method has a single caller thread:
            # __init__ (before anything spins) and the set-parameters
            # callback, which main() runs on the one thread of a
            # single-threaded rclpy.spin(). Two concurrent enables — what a
            # MultiThreadedExecutor would allow — could both pass this check
            # and each create a publisher, silently leaking the loser, and no
            # single-threaded test could see it. Taking _tap_lock here
            # instead was considered and rejected: it would put an RMW
            # publisher create/destroy on the serial reader's critical path
            # (_publish_serial_tap takes the same lock), which is exactly the
            # coupling this lock was split off from self._lock to avoid. So
            # the invariant, not the lock, is what holds: if this node ever
            # moves to a multi-threaded executor, serialise the mutation
            # here (a dedicated mutation lock, or a callback group that
            # keeps the callback mutually exclusive).
            if self._tap_pub is not None:
                return
            # Created outside the lock: create_publisher touches the node's
            # own structures, not the tap reference, and the reader treats a
            # None reference as "off" until the swap lands. A failure
            # therefore leaves _tap_pub None — the tap stays off and the
            # state stays consistent; the caller turns that into a rejected
            # set (see _on_set_parameters).
            pub = self.create_publisher(
                UInt8MultiArray, 'serial_tap', self._topic_qos)
            with self._tap_lock:
                self._tap_pub = pub
        else:
            with self._tap_lock:
                pub, self._tap_pub = self._tap_pub, None
            if pub is not None:
                # Destroyed after the reference is cleared, so no reader can
                # pick it up again while it is being torn down. If the
                # destroy raises, the reference stays dropped rather than
                # being restored: rclpy has already removed the publisher
                # from the node's registry by then, so putting it back would
                # hand the serial thread a publisher nothing owns and
                # destroy_node() would no longer clean up. Dropping it means
                # the tap reads as off, which is what it is — and the
                # serial_tap_enabled diagnostic, derived from _tap_pub,
                # reports that reality even though the rejected set leaves
                # the parameter store saying otherwise.
                self.destroy_publisher(pub)

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
                        # Diagnostic-only, deliberately after the parser feed
                        # so the primary SoundSpeed path is never delayed or
                        # preempted by it (same rule as the raw publish in
                        # _publish_reading). The helper carries its own
                        # exception isolation — see _publish_serial_tap.
                        self._publish_serial_tap(data)
            except (serial.SerialException, OSError) as exc:
                self._serial_connected = False
                self._serial_reconnect_count += 1
                self.get_logger().error(
                    f'Serial error on {self._device}: {exc}; '
                    f'reconnect in {self._reconnect_delay:.1f}s')
                self._stop_event.wait(self._reconnect_delay)
        self._serial_connected = False

    def _publish_serial_tap(self, data: bytes) -> None:
        """
        Publish one pre-framing chunk of the serial stream on ``serial_tap``.

        Publishing is gated on the ``serial_tap_enabled`` parameter, which
        defaults to **off** — the tap is a diagnostic probe, turned on when a
        framing question is being asked and left off the rest of the time.
        While off there is no publisher and the topic is not advertised at
        all. The gate is the publisher reference, read here per chunk rather
        than latched at startup, so ``ros2 param set /sound_speed_bridge
        serial_tap_enabled true`` takes effect on the very next chunk (see
        ``_on_set_parameters`` / ``_set_tap_publishing``).

        ``tap_byte_count`` is incremented *before* the gate as well as before
        the publish, so it keeps counting wire traffic while the tap is
        disabled and its topic does not exist. That is deliberate: it is the
        cheap always-on answer to "is the probe silent?" — an operator who
        sees the counter frozen knows there is nothing to enable the tap
        *for*, without paying any bag volume to find out. Counting a byte
        costs one integer add.

        Isolated from the reader loop on purpose: ``_serial_loop`` catches
        only ``(SerialException, OSError)``, so any other exception escaping
        this diagnostic publish would propagate out of the loop and end the
        serial thread permanently — no reconnect, no readings, in exactly the
        degraded condition this tap exists to observe. A diagnostic must not
        be able to kill the sensor, so the ``except`` is deliberately broad.
        It is not silent: failures are counted (``tap_error_count``) and
        logged (throttled), both visible on ``/diagnostics``.

        ``tap_byte_count`` counts bytes that arrived on the wire, not bytes
        that were successfully published: it is incremented before the
        publish is attempted, so a run of failing publishes shows as bytes
        arriving *and* ``tap_error_count`` climbing, rather than as a silent
        wire. "The probe is silent" is the question this counter answers, and
        only a publish-independent count can answer it. Publish failures are
        ``tap_error_count``'s to report (per chunk, not per byte).
        """
        # Shutdown guard, same rationale as _handle_reading's: destroy_node()'s
        # join is best-effort (2 s) and ser.read() can block up to 1 s past it,
        # after which the publishers may already be destroyed.
        if self._stop_event.is_set():
            return
        self._tap_byte_count += len(data)
        # Local copy under the lock, publish outside it: a concurrent disable
        # can only mean this reference is torn down mid-publish, which the
        # except below turns into a counted tap error (see
        # _set_tap_publishing).
        with self._tap_lock:
            tap_pub = self._tap_pub
        if tap_pub is None:
            return
        try:
            tap_pub.publish(UInt8MultiArray(data=data))
        except Exception as exc:  # noqa: B902 - see docstring
            self._tap_error_count += 1
            self.get_logger().warning(
                f'serial_tap publish failed: {exc}', throttle_duration_sec=10.0)

    def _on_set_parameters(self, params) -> SetParametersResult:
        """
        Apply a runtime ``serial_tap_enabled`` set to the running reader.

        Applying it creates or destroys the ``serial_tap`` publisher
        (``_set_tap_publishing``), so while the tap is off the topic is not
        advertised — an operator's ``ros2 topic list`` shows ``serial_tap``
        exactly when the tap is actually running.

        Only ``serial_tap_enabled`` is applied here; it is the one parameter
        this node declares as runtime-settable. Every other parameter is read
        once in ``__init__``, and this callback deliberately does **not**
        start rejecting runtime sets of them: that would be a behaviour change
        beyond the scope of this issue (``garmin_sidescan``'s node does reject
        them, and broadening the same discipline to this node is a reasonable
        separate change). Accepting them here is exactly what rclpy already
        did before a callback existed, so nothing regresses.

        Type validation is defence in depth: rclpy rejects a type mismatch
        against the declared BOOL type before this callback runs, but the
        callback is the contract an operator (and a test) can rely on, so it
        checks rather than assuming its caller already did. A non-bool is
        rejected with a reason rather than coerced — ``bool('false')`` is
        ``True``, so coercion would silently enable the tap for an operator
        who typed the opposite of what they meant.

        Thread-safety: the publisher swap is done under ``_tap_lock``; see
        ``_set_tap_publishing`` for why that is its own lock and why a
        publish racing a disable is already covered.

        Failure handling: creating or destroying the tap publisher can fail
        (RMW resource exhaustion, a dying context). An exception raised out
        of this callback would propagate through ``rclpy.spin()`` and end the
        process, so the whole bridge would die over a diagnostic topic. It is
        caught and turned into an unsuccessful ``SetParametersResult``
        instead: the set is rejected, the parameter keeps its previous value,
        the node keeps publishing SoundSpeed, and the operator sees the
        reason in the ``ros2 param set`` response and in the log.
        """
        requested = None
        for param in params:
            if param.name != 'serial_tap_enabled':
                continue
            if param.type_ != Parameter.Type.BOOL:
                reason = (
                    f'serial_tap_enabled must be a bool, got '
                    f'{param.type_.name.lower()}')
                self.get_logger().warning(f'{reason}; rejected')
                return SetParametersResult(successful=False, reason=reason)
            requested = bool(param.value)

        if requested is not None:
            try:
                self._set_tap_publishing(requested)
            except Exception as exc:  # noqa: B902 - see docstring
                # An RMW/resource failure creating or destroying the tap
                # publisher must not take the bridge down with it. rclpy
                # wraps on-set callbacks in no try of its own and its
                # executor re-raises a handler exception straight out of
                # rclpy.spin(), which main() guards only for
                # KeyboardInterrupt — so without this, a failed `ros2 param
                # set serial_tap_enabled true` would kill the primary
                # SoundSpeed/Temperature/FluidPressure publishing. Same rule
                # as _publish_serial_tap's broad except: a diagnostic must
                # not be able to kill the sensor. The failure degrades to a
                # rejected set (the parameter store keeps its old value), is
                # logged at ERROR with the exception, and leaves the tap
                # state consistent — see _set_tap_publishing.
                reason = (
                    f'serial_tap_enabled={requested} could not be applied: '
                    f'{exc!r}')
                self.get_logger().error(f'{reason}; rejected')
                return SetParametersResult(successful=False, reason=reason)
            # Logged on every accepted set, not only on a transition: the
            # operator needs confirmation that the command landed, and a
            # re-set to the current value is an operator asking exactly that.
            self.get_logger().info(
                'serial_tap ENABLED - advertising and publishing the '
                'pre-framing wire stream'
                if requested else
                'serial_tap disabled - topic unadvertised, no messages will be '
                'published (tap_byte_count keeps counting wire bytes)')
        return SetParametersResult(successful=True)

    def _handle_reading(self, reading: SoundSpeedReading) -> None:
        """
        Record and publish one reading. Runs on the serial thread.

        The body lives in :meth:`_publish_reading` so the shutdown guard
        below wraps a single call rather than fifty lines.
        """
        # Shutdown guard: destroy_node()'s join is best-effort (2 s) — a read
        # wedged in the UART layer can outlast it, after which the publishers
        # are destroyed while this daemon thread still runs. Once the stop
        # event is set, publishing is no longer safe.
        if self._stop_event.is_set():
            return
        try:
            self._publish_reading(reading)
        except (_rclpy.RCLError, InvalidHandle):
            # [SW4] call-level guard, the same one _publish_diagnostics
            # carries, for the same reason one layer over. The stop-event
            # test above is check-then-act: rclpy's signal handler can tear
            # the context down in the gap between it and any publish below,
            # and rcl then raises "Failed to publish: publisher's context is
            # invalid". Nothing on this thread would catch it — _serial_loop
            # catches only (SerialException, OSError) — so a deliberate
            # Ctrl-C ends the serial reader with a thread traceback.
            # InvalidHandle is the same condition one step later, once
            # destroy_node() has taken the publisher handles.
            #
            # ok() is consulted only *after* the failure, never before it, so
            # the decision is made on what actually happened: a shutdown in
            # flight returns quietly; the same failure on a live context is
            # re-raised unchanged, so a publisher that has stopped working
            # mid-deployment is still loud. Non-RCL exceptions are
            # deliberately not caught — a formatter or socket bug must still
            # surface.
            if not rclpy.ok(context=self.context):
                return
            raise

    def _publish_reading(self, reading: SoundSpeedReading) -> None:
        """
        Publish one reading on every configured sink (serial thread).

        Every RCL call on this path — ``sound_speed``, ``raw``, the optional
        ``temperature``/``pressure``, and the logging inside the UDP
        error path — is covered by :meth:`_handle_reading`'s guard.
        """
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
        # WARN below and the KeyValues published from it. Read as a single
        # tuple: the pair is written on the serial thread, and two separate
        # reads can straddle a trim, reporting a trim count without the
        # bytes that go with it -- numbers that never coexisted, in a log
        # line and a KeyValue an operator is expected to correlate.
        dropped_bytes, trim_count = self._parser.trim_stats
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
            # Aliveness of the pre-framing tap, so "the probe is silent" can be
            # told from "the node never ran / serial_tap was not recorded"
            # without inspecting bag content. tap_byte_count is bytes read off
            # the wire, counted whether or not the publish succeeded, so the
            # two keys separate "no bytes arrived" from "bytes arrived but the
            # tap could not publish them". This requires /diagnostics to be in
            # the deployment bag record list
            # (rolker/unh_echoboats_project11#396) alongside serial_tap.
            KeyValue(key='tap_byte_count', value=str(self._tap_byte_count)),
            KeyValue(key='tap_error_count', value=str(self._tap_error_count)),
            # Whether the tap is currently advertised and publishing.
            # Without this key an absence of serial_tap messages in a bag is
            # ambiguous between "the probe was silent" and "the tap was never
            # switched on" — and since the tap now defaults to off, and its
            # topic does not even exist while off, the second reading is the
            # likely one. tap_byte_count separates silent from corrupt; this
            # key separates both from not-enabled.
            KeyValue(key='serial_tap_enabled',
                     value=str(self._serial_tap_enabled).lower()),
            KeyValue(key='device', value=self._device),
            KeyValue(key='parser', value=self._parser_name),
        ]

        diag_msg = DiagnosticArray()
        diag_msg.header.stamp = self.get_clock().now().to_msg()
        diag_msg.status = [status]
        try:
            self._diag_pub.publish(diag_msg)
        except (_rclpy.RCLError, InvalidHandle):
            # rclpy's signal handler tears the context down while the
            # executor is still inside spin(), so this timer can reach
            # publish() after the publisher's context has gone invalid.
            # rcl then raises "Failed to publish: publisher's context is
            # invalid", spin() propagates it, and a deliberate stop exits 1
            # with a traceback -- indistinguishable from a crash under
            # systemd Restart=on-failure, which is the operator-facing
            # contract main() restores one layer out. InvalidHandle is the
            # same condition one step later (the node is destroyed and the
            # publisher handle is gone).
            #
            # The call is guarded rather than preceded by an `if rclpy.ok()`
            # test: that would be check-then-act and the shutdown can land in
            # the gap. ok() is consulted only afterwards, to decide what the
            # failure meant -- a shutdown in flight returns quietly, a
            # failure on a live context is re-raised unchanged, so a genuine
            # fault is still loud. (garmin_sidescan carries the same guard as
            # a `quiet_on_shutdown` decorator because it has many such call
            # sites; here one publish needs only these lines.)
            if not rclpy.ok(context=self.context):
                return
            raise

    def destroy_node(self) -> bool:
        """Stop the serial thread and close the UDP socket before shutdown."""
        self._stop_event.set()
        if self._serial_thread.is_alive():
            self._serial_thread.join(timeout=2.0)
        if self._udp_socket is not None:
            self._udp_socket.close()
        return super().destroy_node()


def main(args=None) -> None:
    """
    Entry point: spin the bridge node until interrupted.

    Construction has its own try so a parameter the node refuses
    (see :meth:`SoundSpeedBridgeNode._validated_max_buffer_bytes`) is
    reported as one FATAL line naming the parameter, rclpy is still shut
    down, and the process exits **non-zero** -- ``ros2 launch`` and
    systemd ``Restart=on-failure`` must see a refused start as a failure,
    not a clean shutdown. The node still refuses to start; that is
    deliberate. A ValueError raised later, from a callback during spin,
    is not a start failure and is left to propagate as before.

    A deliberate stop is exit 0. rclpy installs its own SIGINT
    handler, which shuts the context down *before* the handler here runs:
    ``spin()`` then raises ``ExternalShutdownException`` (uncaught, exit 1)
    and a plain ``rclpy.shutdown()`` in the ``finally`` raises ``RCLError:
    rcl_shutdown already called``. ``try_shutdown()`` is the idempotent
    form, and it still shuts down when the process ends any other way.
    Under ``Restart=on-failure`` the difference decides whether an operator
    stopping a node gets it restarted under them.
    """
    rclpy.init(args=args)
    node = None
    try:
        try:
            node = SoundSpeedBridgeNode()
        except ValueError as exc:
            rclpy.logging.get_logger('sound_speed_bridge').fatal(
                f'sound_speed_bridge failed to start: {exc}')
            raise SystemExit(1) from exc
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
