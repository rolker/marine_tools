"""
Garmin GCV-10/20 sidescan sonar driver node.

Receives the GCV imagery multicast, decodes per-ping sidescan scan lines (see
:mod:`garmin_sidescan.decode`) and publishes them as
``marine_acoustic_msgs/RawSonarImage`` (one publisher per channel, single
beam), plus an optional rolling-waterfall ``sensor_msgs/Image`` for operator
viewing.  Controls transmit on/off and range over the GCV TCP command port
(see :mod:`garmin_sidescan.commands`).

Safety: the node asserts transmit OFF at startup and never pings without an
explicit command.  While transmitting, a sound-speed watchdog stops the sonar
if the sound speed reads NaN / 0 / out-of-water for ``sv_timeout`` seconds, so
a dry transducer cannot overheat.  The whole mechanism has a dynamic master
switch (``sound_speed_safety_enabled``).
"""
from collections import deque
import math
import socket
import threading
import time

from marine_acoustic_msgs.msg import RawSonarImage, SonarImageData
from marine_radar_control_msgs.msg import RadarControlItem, RadarControlSet, RadarControlValue
from rcl_interfaces.msg import SetParametersResult
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rosidl_runtime_py.utilities import get_message
from sensor_msgs.msg import Image
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool

from .commands import (
    build_interference_cmd,
    build_range_cmd,
    build_tvg_cmd,
    LOW_MED_HIGH,
    TRANSMIT_OFF,
    TRANSMIT_ON,
)
from .decode import PingAssembler

SIDES = ('port', 'stbd', 'clearvu')


def transmit_state_after(commanded_on, send_ok, prior):
    """
    Return the transmit state to record after issuing a command.

    A failed OFF must not be recorded as OFF: the sonar may still be pinging,
    so we stay True and let the watchdog keep retrying.  A successful command
    records the commanded state.  A failed ON keeps the ``prior`` state rather
    than asserting OFF: if the sonar may already be pinging (e.g. a preceding
    OFF that also failed left ``prior`` True, then an auto-resume ON send fails
    too), recording OFF would falsely disarm the watchdog over a live, dry
    transducer.
    """
    if not commanded_on:
        return not send_ok
    return True if send_ok else prior


def watchdog_action(*, safety_enabled, transmitting, has_sv_topic,
                    require_sv, sv_age, sv_timeout):
    """
    Decide whether the sound-speed watchdog must stop transmit.

    Returns ``(stop, kind)`` where ``stop`` is a bool and ``kind`` is
    ``''`` (no action), ``'no_source'`` (require_sound_speed set but no
    sound-speed source configured), or ``'stale'`` (no valid reading within
    ``sv_timeout``).

    The watchdog runs independently of *require_sound_speed*: that flag governs
    whether a fresh reading is required *before* transmitting, while the
    watchdog must stop a dry transducer whenever it is (or may be) transmitting.
    With no sound-speed source it mirrors ``_guard_transmit_on``: under
    require_sv it must stop (the guard refuses to *start* here, so a
    maybe-transmitting state reached without a guard must not be left pinging);
    without require_sv (bench testing) there is nothing to evaluate, so it
    leaves transmit untouched.
    """
    if not safety_enabled or not transmitting:
        return False, ''
    if not has_sv_topic:
        return (True, 'no_source') if require_sv else (False, '')
    if sv_age is None or sv_age > sv_timeout:
        return True, 'stale'
    return False, ''


def range_in_bounds(meters, range_min, range_max):
    """Return whether a requested range (m) is within the configured limits."""
    return range_min <= meters <= range_max


class GarminSidescanNode(Node):
    """Driver node: GCV imagery in, RawSonarImage out, transmit under safety."""

    def __init__(self):
        super().__init__('garmin_sidescan')

        # network / device
        self.declare_parameter('gcv_ip', '172.16.3.0')        # GCV-10 = 172.16.3.196
        self.declare_parameter('control_port', 50227)
        self.declare_parameter('mcast_group', '239.254.2.1')
        self.declare_parameter('mcast_port', 50220)
        self.declare_parameter('iface_ip', '')                 # local NIC for the join
        self.declare_parameter('filter_src', True)
        self.declare_parameter('frame_id', 'gcv_sonar')

        # channel map (GCV-20: 0=port 1=stbd 2=clearvu; GCV-10 data: port=[3] stbd=[1])
        self.declare_parameter('port_channels', [0])
        self.declare_parameter('stbd_channels', [1])
        self.declare_parameter('clearvu_channels', [2])

        # frequency is NOT carried in the imagery stream; set per transducer or
        # leave 0.0 = unavailable (RawSonarImage/PingInfo convention).
        self.declare_parameter('freq_port_hz', 0.0)
        self.declare_parameter('freq_stbd_hz', 0.0)
        self.declare_parameter('freq_clearvu_hz', 0.0)
        self.declare_parameter('sample_rate_hz', 0.0)

        # imagery rendering
        self.declare_parameter('range_bins', 0)                # 0 = lock from first ping
        self.declare_parameter('waterfall_height', 600)
        self.declare_parameter('waterfall_rate_hz', 4.0)
        self.declare_parameter('publish_waterfall', True)

        # transmit / safety
        self.declare_parameter('transmit_on_startup', False)
        self.declare_parameter('startup_off_repeats', 3)
        self.declare_parameter('range_m', 0.0)

        # operator control set (radar-style; rendered by CAMP)
        self.declare_parameter('range_min_m', 1.0)
        self.declare_parameter('range_max_m', 60.0)
        # TVG / interference frames are GCV-10-derived and unverified on the
        # GCV-20 (TVG is display-side there); expose them but allow opting out.
        self.declare_parameter('expose_gcv10_controls', True)

        # sound-speed watchdog
        self.declare_parameter('sound_speed_safety_enabled', True)
        self.declare_parameter('sound_speed_topic',
                               '/bizzy/sensors/sound_speed/sound_speed')
        self.declare_parameter('sound_speed_type', 'marine_interfaces/msg/SoundSpeed')
        self.declare_parameter('sound_speed_field', 'sound_speed')
        self.declare_parameter('sv_min', 1400.0)
        self.declare_parameter('sv_max', 1600.0)
        self.declare_parameter('sv_timeout', 12.0)
        self.declare_parameter('require_sound_speed', True)
        self.declare_parameter('auto_resume', True)
        self.declare_parameter('resume_valid_samples', 3)     # sustained-valid before resume

        self._gcv_ip = self._p('gcv_ip')
        self._ctrl_port = int(self._p('control_port'))
        self._group = self._p('mcast_group')
        self._mport = int(self._p('mcast_port'))
        self._iface_ip = self._p('iface_ip')
        self._filter_src = bool(self._p('filter_src'))
        self._frame_id = self._p('frame_id')
        self._chan_side = {}
        for ch in self._p('port_channels'):
            self._chan_side[ch] = 'port'
        for ch in self._p('stbd_channels'):
            self._chan_side[ch] = 'stbd'
        for ch in self._p('clearvu_channels'):
            self._chan_side[ch] = 'clearvu'
        self._freq = {
            'port': float(self._p('freq_port_hz')),
            'stbd': float(self._p('freq_stbd_hz')),
            'clearvu': float(self._p('freq_clearvu_hz')),
        }
        self._sample_rate = float(self._p('sample_rate_hz'))
        self._range_bins = int(self._p('range_bins'))
        self._wf_h = int(self._p('waterfall_height'))
        self._publish_wf = bool(self._p('publish_waterfall'))
        self._sv_topic = self._p('sound_speed_topic')
        self._sv_field = self._p('sound_speed_field')
        self._sv_min = float(self._p('sv_min'))
        self._sv_max = float(self._p('sv_max'))
        self._sv_timeout = float(self._p('sv_timeout'))
        self._require_sv = bool(self._p('require_sound_speed'))
        self._auto_resume = bool(self._p('auto_resume'))
        self._resume_valid_samples = max(1, int(self._p('resume_valid_samples')))
        self._safety_enabled = bool(self._p('sound_speed_safety_enabled'))

        # state
        self._tx_lock = threading.Lock()      # guards transmit/latch state changes
        self._send_lock = threading.Lock()    # serializes TCP command sends
        self._transmitting = False
        self._safety_latched = False
        self._last_valid_sv_t = None
        self._last_valid_sv_value = 0.0
        self._last_sv_value = float('nan')
        self._valid_streak = 0
        self._ping_count = {s: 0 for s in SIDES}
        self._range_min = float(self._p('range_min_m'))
        self._range_max = float(self._p('range_max_m'))
        self._expose_gcv10 = bool(self._p('expose_gcv10_controls'))
        # current operator-control values (strings, radar-control convention)
        start_range = float(self._p('range_m'))
        self._controls = {
            'status': 'standby',
            'range': f'{start_range:.1f}',
            'tvg': 'off',
            'interference': 'off',
        }
        self._assembler = PingAssembler()
        self._wf = {'port': deque(maxlen=self._wf_h), 'stbd': deque(maxlen=self._wf_h)}
        self._wf_lock = threading.Lock()
        self._width = {}
        self._running = True

        # publishers
        img_qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT)
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._pub_sonar = {
            'port': self.create_publisher(RawSonarImage, '~/sonar_image_port', img_qos),
            'stbd': self.create_publisher(RawSonarImage, '~/sonar_image_starboard', img_qos),
            'clearvu': self.create_publisher(RawSonarImage, '~/sonar_image_clearvu', img_qos),
        }
        self._pub_wf = {
            'port': self.create_publisher(Image, '~/waterfall_port', img_qos),
            'stbd': self.create_publisher(Image, '~/waterfall_starboard', img_qos),
        }
        self._pub_tx = self.create_publisher(Bool, '~/transmitting', latched)
        self._pub_status = self.create_publisher(String, '~/status', latched)
        self._pub_state = self.create_publisher(RadarControlSet, '~/state', latched)

        self.create_service(SetBool, '~/set_transmit', self._on_set_transmit)
        self.create_subscription(RadarControlValue, '~/change_state',
                                 self._on_control_value, 10)

        if self._sv_topic:
            try:
                sv_type = get_message(self._p('sound_speed_type'))
                # Default RELIABLE depth-10 matches sound_speed_bridge's publisher
                # QoS; a BEST_EFFORT source would need this adjusted to match, or
                # the watchdog would silently receive nothing.
                self.create_subscription(sv_type, self._sv_topic, self._on_sound_speed, 10)
                self.get_logger().info(
                    f'sound-speed watchdog on {self._sv_topic} '
                    f'(valid {self._sv_min}-{self._sv_max} m/s, timeout {self._sv_timeout}s)')
            except (ValueError, ImportError, AttributeError) as exc:
                self.get_logger().error(
                    f'could not subscribe to sound speed ({exc}); watchdog DISABLED '
                    '- transmit refused unless require_sound_speed:=false')
                self._sv_topic = ''
        else:
            self.get_logger().warn('sound_speed_topic empty: watchdog disabled')

        self.add_on_set_parameters_callback(self._on_param_set)

        self._rx_thread = threading.Thread(target=self._rx_loop, name='gcv_rx', daemon=True)
        self._rx_thread.start()
        self.create_timer(0.5, self._watchdog)
        if self._publish_wf:
            rate = max(0.5, float(self._p('waterfall_rate_hz')))
            self.create_timer(1.0 / rate, self._publish_waterfalls)
        self.create_timer(2.0, self._publish_status)

        self._publish_tx_state()
        threading.Thread(
            target=self._startup_transmit_state,
            args=(bool(self._p('transmit_on_startup')), int(self._p('startup_off_repeats')),
                  float(self._p('range_m'))),
            name='gcv_startup', daemon=True).start()

    def _p(self, name):
        """Return a declared parameter's value."""
        return self.get_parameter(name).value

    # ----- startup -----------------------------------------------------------
    def _startup_transmit_state(self, want_on, off_repeats, range_m):
        with self._tx_lock:
            ok = False
            for _ in range(max(1, off_repeats)):
                if self._send(TRANSMIT_OFF):
                    ok = True
                time.sleep(0.3)
            # If every OFF send failed the GCV may be pinging; stay "on" so
            # the watchdog keeps retrying rather than reporting a false OFF.
            self._transmitting = not ok
        self._publish_tx_state()
        if ok:
            self.get_logger().info('startup: transmit asserted OFF')
        else:
            self.get_logger().error(
                'startup: could not assert transmit OFF (GCV unreachable?); '
                'assuming sonar may be pinging - watchdog will retry')
        if range_m > 0:
            if self._send(build_range_cmd(range_m)):
                self._controls['range'] = f'{range_m:.1f}'
                self.get_logger().info(f'startup: range set to {range_m} m')
            else:
                self.get_logger().error(f'startup: range command ({range_m} m) failed to send')
        if want_on:
            ok, msg = self._guard_transmit_on()
            if ok:
                self._set_transmit(True, 'startup (transmit_on_startup)')
            else:
                self.get_logger().warn(f'startup transmit_on_startup refused: {msg}')

    # ----- TCP command send --------------------------------------------------
    def _send(self, data):
        """Open the GCV control port, send a command frame, and close."""
        with self._send_lock:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(2.0)
                    sock.connect((self._gcv_ip, self._ctrl_port))
                    sock.sendall(data)
                return True
            except OSError as exc:
                self.get_logger().warn(
                    f'GCV command send failed ({self._gcv_ip}:{self._ctrl_port}): {exc}')
                return False

    def _set_transmit(self, on, reason):
        with self._tx_lock:
            ok = self._send(TRANSMIT_ON if on else TRANSMIT_OFF)
            # A failed OFF keeps _transmitting True so the watchdog keeps
            # retrying and status reflects reality; a failed ON keeps the prior
            # state so an auto-resume ON failing after a failed OFF can't
            # falsely report OFF (see transmit_state_after).
            self._transmitting = transmit_state_after(on, ok, prior=self._transmitting)
            if on and ok:
                self._safety_latched = False
        if not on and not ok:
            self.get_logger().error(
                f'transmit OFF command FAILED ({reason}); sonar may still be '
                'pinging - will retry')
        else:
            log = self.get_logger().error if (not on and reason.startswith('SAFETY')) \
                else self.get_logger().info
            log(f'transmit {"ON" if self._transmitting else "OFF"} ({reason})')
        self._publish_tx_state()

    def _publish_tx_state(self):
        self._pub_tx.publish(Bool(data=bool(self._transmitting)))
        # keep the operator-control mirror in sync (incl. watchdog-driven changes)
        self._controls['status'] = 'transmit' if self._transmitting else 'standby'
        self._publish_control_set()

    def _request_transmit(self, on):
        """
        Guarded transmit request shared by the service and control set.

        The return reflects the actual resulting transmit state, not merely
        that a command was attempted: a failed ON send reports failure, and a
        failed OFF send reports that the sonar may still be transmitting.
        """
        if on:
            ok, msg = self._guard_transmit_on()
            if not ok:
                self.get_logger().warn(f'transmit ON refused: {msg}')
                return False, msg
            self._set_transmit(True, 'request')
            if self._transmitting:
                return True, 'transmitting'
            return False, 'transmit ON command failed to send'
        self._safety_latched = False    # explicit operator off: no auto-resume
        self._set_transmit(False, 'request')
        if self._transmitting:
            return False, 'transmit OFF command failed to send; sonar may still be pinging'
        return True, 'transmit off'

    # ----- transmit guard ----------------------------------------------------
    def _sv_age(self):
        if self._last_valid_sv_t is None:
            return None
        return time.monotonic() - self._last_valid_sv_t

    def _current_sound_speed(self):
        """Last valid sound speed if still fresh, else 0.0 (unavailable)."""
        age = self._sv_age()
        if age is None or age > self._sv_timeout:
            return 0.0
        return self._last_valid_sv_value

    def _guard_transmit_on(self):
        if not self._safety_enabled or not self._require_sv:
            return True, ''
        if not self._sv_topic:
            return False, ('sound_speed_topic not configured; refusing to transmit '
                           '(override with require_sound_speed:=false)')
        age = self._sv_age()
        if age is None:
            return False, 'no valid sound speed received yet; refusing to transmit'
        if age > self._sv_timeout:
            return False, (f'last valid sound speed was {age:.1f}s ago '
                           f'(>{self._sv_timeout}s); transducer may be out of water')
        return True, ''

    def _on_set_transmit(self, req, resp):
        resp.success, resp.message = self._request_transmit(bool(req.data))
        return resp

    # ----- operator control set (radar-style) -------------------------------
    def _publish_control_set(self):
        rcs = RadarControlSet()
        status = RadarControlItem()
        status.name = 'status'
        status.label = 'Status'
        status.type = RadarControlItem.CONTROL_TYPE_ENUM
        status.value = self._controls['status']
        status.enums = ['standby', 'transmit']
        rcs.items.append(status)

        rng = RadarControlItem()
        rng.name = 'range'
        rng.label = 'Range (m)'
        rng.type = RadarControlItem.CONTROL_TYPE_FLOAT
        rng.value = self._controls['range']
        rng.min_value = self._range_min
        rng.max_value = self._range_max
        rcs.items.append(rng)

        if self._expose_gcv10:
            for name, label in (('tvg', 'TVG'), ('interference', 'Interference')):
                item = RadarControlItem()
                item.name = name
                item.label = label
                item.type = RadarControlItem.CONTROL_TYPE_ENUM
                item.value = self._controls[name]
                item.enums = list(LOW_MED_HIGH)
                rcs.items.append(item)
        self._pub_state.publish(rcs)

    def _on_control_value(self, msg):
        key, value = msg.key, msg.value
        if key == 'status':
            self._request_transmit(value == 'transmit')
            return                       # _request_transmit republishes the set
        elif key == 'range':
            try:
                meters = float(value)
            except ValueError:
                self.get_logger().warn(f'bad range control value: {value!r}')
                return
            meters = max(self._range_min, min(self._range_max, meters))
            # Only mirror/log success if the command actually sent; otherwise
            # CAMP would show a range the GCV never received.
            if self._send(build_range_cmd(meters)):
                self._controls['range'] = f'{meters:.1f}'
                self.get_logger().info(f'range set to {meters} m (control)')
            else:
                self.get_logger().error(f'range command ({meters} m) failed to send')
        elif key in ('tvg', 'interference') and self._expose_gcv10:
            if value not in LOW_MED_HIGH:
                self.get_logger().warn(f'bad {key} control value: {value!r}')
                return
            level = LOW_MED_HIGH.index(value)
            builder = build_tvg_cmd if key == 'tvg' else build_interference_cmd
            if self._send(builder(level)):
                self._controls[key] = value
                self.get_logger().info(f'{key} set to {value} (control)')
            else:
                self.get_logger().error(f'{key} command ({value}) failed to send')
        else:
            self.get_logger().warn(f'unknown control key: {key!r}')
            return
        self._publish_control_set()

    # ----- sound-speed watchdog ---------------------------------------------
    def _extract_field(self, msg):
        val = msg
        for part in self._sv_field.split('.'):
            val = getattr(val, part)
        return float(val)

    def _on_sound_speed(self, msg):
        try:
            sv = self._extract_field(msg)
        except (AttributeError, TypeError, ValueError) as exc:
            self.get_logger().warn(
                f'sound-speed field "{self._sv_field}" unreadable: {exc}',
                throttle_duration_sec=10.0)
            return
        self._last_sv_value = sv
        if math.isfinite(sv) and self._sv_min <= sv <= self._sv_max:
            self._last_valid_sv_t = time.monotonic()
            self._last_valid_sv_value = sv
            self._valid_streak += 1
            # Resume only after sustained recovery, and only if the full
            # transmit guard (master switch, freshness) still passes — a
            # single in-range sample must not re-energize a dry transducer.
            if (self._safety_latched and self._auto_resume and self._safety_enabled
                    and self._valid_streak >= self._resume_valid_samples):
                ok, _msg = self._guard_transmit_on()
                if ok:
                    self.get_logger().info(
                        f'sound speed recovered ({sv:.1f} m/s, '
                        f'{self._valid_streak} samples); auto-resuming transmit')
                    self._set_transmit(True, 'auto_resume after sustained recovery')
        else:
            self._valid_streak = 0

    def _watchdog(self):
        age = self._sv_age()
        stop, kind = watchdog_action(
            safety_enabled=self._safety_enabled,
            transmitting=self._transmitting,
            has_sv_topic=bool(self._sv_topic),
            require_sv=self._require_sv,
            sv_age=age,
            sv_timeout=self._sv_timeout)
        if not stop:
            return
        self._safety_latched = True
        if kind == 'no_source':
            reason = ('SAFETY: require_sound_speed set but no sound-speed source '
                      'configured; stopping ping to protect transducer')
        else:
            shown = 'never' if age is None else f'{age:.1f}s ago'
            reason = (f'SAFETY: sound speed invalid/stale (last valid {shown}, '
                      f'value={self._last_sv_value}); stopping ping to protect transducer')
        self._set_transmit(False, reason)

    # ----- imagery receive + decode -----------------------------------------
    def _open_mcast(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', self._mport))
        iface = socket.inet_aton(self._iface_ip) if self._iface_ip \
            else socket.inet_aton('0.0.0.0')
        mreq = socket.inet_aton(self._group) + iface
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1.0)
        return sock

    def _rx_loop(self):
        sock = None
        while self._running:
            if sock is None:
                try:
                    sock = self._open_mcast()
                    self.get_logger().info(
                        f'joined {self._group}:{self._mport}'
                        + (f' on {self._iface_ip}' if self._iface_ip else ''))
                except OSError as exc:
                    self.get_logger().error(f'multicast join failed: {exc}; retrying',
                                            throttle_duration_sec=5.0)
                    time.sleep(2.0)
                    continue
            try:
                payload, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                # Close before dropping the reference so the fd (and the
                # multicast membership) is released rather than leaked on a
                # rejoin loop.
                sock.close()
                sock = None
                continue
            if self._filter_src and addr[0] != self._gcv_ip:
                continue
            now = self.get_clock().now()
            for ch, samples, stamp in self._assembler.feed(payload, now):
                self._emit_ping(ch, samples, stamp)
        for ch, samples, stamp in self._assembler.flush():
            self._emit_ping(ch, samples, stamp)
        if sock is not None:
            sock.close()

    def _emit_ping(self, ch, samples, stamp):
        side = self._chan_side.get(ch)
        if side is None or not samples:
            return
        self._ping_count[side] += 1
        self._pub_sonar[side].publish(self._make_sonar_msg(side, samples, stamp))
        if self._publish_wf and side in self._wf:
            width = self._width.get(side)
            if width is None:
                width = self._range_bins if self._range_bins > 0 else len(samples)
                self._width[side] = width
            row = samples[:width] + bytes(max(0, width - len(samples)))
            with self._wf_lock:
                self._wf[side].append(row)

    def _make_sonar_msg(self, side, samples, stamp):
        msg = RawSonarImage()
        msg.header.stamp = stamp.to_msg()
        msg.header.frame_id = self._frame_id
        msg.ping_info.frequency = self._freq[side]
        msg.ping_info.sound_speed = self._current_sound_speed()
        msg.sample_rate = self._sample_rate
        msg.samples_per_beam = len(samples)
        msg.sample0 = 0
        msg.tx_delays = [0.0]
        msg.tx_angles = [0.0]
        msg.rx_angles = [0.0]
        msg.image.is_bigendian = False
        msg.image.dtype = SonarImageData.DTYPE_UINT8
        msg.image.beam_count = 1
        msg.image.data = bytes(samples)
        return msg

    def _publish_waterfalls(self):
        stamp = self.get_clock().now().to_msg()
        for side in ('port', 'stbd'):
            with self._wf_lock:
                rows = list(self._wf[side])
                width = self._width.get(side)
            if not rows or not width:
                continue
            img = Image()
            img.header.stamp = stamp
            img.header.frame_id = self._frame_id
            img.height = len(rows)
            img.width = width
            img.encoding = 'mono8'
            img.is_bigendian = 0
            img.step = width
            img.data = b''.join(rows)
            self._pub_wf[side].publish(img)

    def _publish_status(self):
        age = self._sv_age()
        age_s = 'n/a' if age is None else f'{age:.1f}s'
        self._pub_status.publish(String(data=(
            f'tx={"ON" if self._transmitting else "OFF"} '
            f'safety={"on" if self._safety_enabled else "OFF"} '
            f'require_sv={self._require_sv} '
            f'latched={self._safety_latched} '
            f'sv={self._last_sv_value:.1f} sv_age={age_s} '
            f'pings(port/stbd/cv)={self._ping_count["port"]}/'
            f'{self._ping_count["stbd"]}/{self._ping_count["clearvu"]}')))

    def _on_param_set(self, params):
        # Validate the whole batch before applying ANY side effect. rclpy
        # accepts/rejects a set_parameters() call atomically on the single
        # returned result, so applying a side effect (sending the range command,
        # mirroring it to the UI) for one param and then rejecting the batch
        # because of a *different* param would desync the param store from the
        # hardware/UI. Side effects happen only once every param is acceptable.
        range_request = None
        for p in params:
            if p.name == 'range_m':
                meters = float(p.value)
                # Reject (don't silently accept) a non-finite or out-of-range
                # request - including <=0 and NaN, which previously slipped
                # through the truthiness guard and were reported successful while
                # the node ignored them. Mirrors the control-set range guard.
                if not math.isfinite(meters) or not range_in_bounds(
                        meters, self._range_min, self._range_max):
                    self.get_logger().warn(
                        f'range_m {meters} m invalid or outside '
                        f'{self._range_min}-{self._range_max} m; rejected')
                    return SetParametersResult(
                        successful=False,
                        reason=(f'range {meters} m invalid or outside '
                                f'{self._range_min}-{self._range_max} m'))
                range_request = meters
            elif p.name == 'sound_speed_safety_enabled':
                pass  # always acceptable; applied below
            elif p.name != 'use_sim_time' and self.has_parameter(p.name):
                # Every other declared parameter is read once at startup. Silently
                # accepting a runtime set would report success while the node keeps
                # the cached value, so an operator/UI would believe a setting took
                # effect that didn't. Reject it explicitly. (use_sim_time is an
                # rclpy built-in left to default handling; undeclared names, which
                # has_parameter rejects, fall through.)
                self.get_logger().warn(
                    f'{p.name} is a startup parameter; runtime updates are not '
                    f'applied - rejected')
                return SetParametersResult(
                    successful=False,
                    reason=f'{p.name} is set at launch, not at runtime')

        # Every param is acceptable. Apply the fallible hardware command first,
        # so a send failure rejects the batch before any local state is mutated;
        # the ROS param and UI never claim a range the GCV didn't apply.
        if range_request is not None:
            if not self._send(build_range_cmd(range_request)):
                self.get_logger().error(
                    f'range command ({range_request} m) failed to send')
                return SetParametersResult(
                    successful=False, reason='range command failed to send')
            self._controls['range'] = f'{range_request:.1f}'
            self._publish_control_set()
            self.get_logger().info(f'range set to {range_request} m')

        for p in params:
            if p.name == 'sound_speed_safety_enabled':
                self._safety_enabled = bool(p.value)
                if self._safety_enabled:
                    self.get_logger().info('sound-speed safety mechanism ENABLED')
                else:
                    self.get_logger().warn(
                        'sound-speed safety mechanism DISABLED (watchdog auto-stop and '
                        'transmit guard off - dry-transducer protection is not active)')
        return SetParametersResult(successful=True)

    def destroy_node(self):
        """Stop the receive loop and assert transmit off on shutdown."""
        self._running = False
        # Retry the OFF; a single dropped frame on shutdown must not leave a
        # dry transducer pinging. _send already swallows OSError -> bool.
        off_ok = False
        for _ in range(3):
            if self._send(TRANSMIT_OFF):
                off_ok = True
                break
        if not off_ok:
            self.get_logger().error('shutdown: could not confirm transmit OFF')
        super().destroy_node()


def main():
    """Entry point."""
    rclpy.init()
    node = GarminSidescanNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
