"""
Garmin GCV-10/20 sidescan sonar driver node.

Receives the GCV imagery multicast, decodes per-ping sidescan scan lines (see
:mod:`garmin_sidescan.decode`) and publishes them as
``marine_acoustic_msgs/RawSonarImage`` (one publisher per channel, single
beam) with the metric scale (``sample_rate``) derived from each ping's own
sub-header display range (v2), plus the per-ping nadir bottom range
(sub-header v1) as a ``sensor_msgs/Range`` on ``~/nadir_depth`` and the
transducer surface water temperature (the d807 telemetry marker) as a
``sensor_msgs/Temperature`` on ``~/water_temperature``.  Controls
transmit on/off and range over the GCV TCP command port
(see :mod:`garmin_sidescan.commands`).  Rendering is left to downstream tools
(``rqt_sonar_waterfall``); a ``debug_raw`` parameter can publish the raw UDP
payloads on ``~/debug/raw`` for offline re-decode.

Safety: the node asserts transmit OFF at startup and never pings without an
explicit command.  The GCV stops pinging on its own when out of the water, so
no external sound-speed interlock is needed to protect the transducer.
"""
import math
import socket
import threading
import time

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from marine_acoustic_msgs.msg import RawSonarImage, SonarImageData
from marine_control_py import ControlServer
from marine_radar_control_msgs.msg import RadarControlItem, RadarControlSet, RadarControlValue
from rcl_interfaces.msg import FloatingPointRange, ParameterDescriptor, SetParametersResult
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Range, Temperature
from std_msgs.msg import Bool, String, UInt8MultiArray
from std_srvs.srv import SetBool

from .commands import (
    build_interference_cmd,
    build_range_cmd,
    build_tvg_cmd,
    LOW_MED_HIGH,
    TRANSMIT_OFF,
    TRANSMIT_ON,
)
from .decode import (
    CHANNEL_OFFSET, derive_sample_rate, EB07,
    generation_from_layers, GRID_BINS, is_water_column, marker_temperature_c,
    MIN_DATA_LEN, PingAssembler, status_transmitting)

# Auxiliary GCV multicast streams the driver can listen to. The imagery group
# is a parameter (mcast_group/port); these two are fixed by the GCV protocol.
STATUS_GROUP, STATUS_PORT = '239.254.2.2', 50050    # 8e03 status (tx flag)
CONFIG_GROUP, CONFIG_PORT = '239.254.2.11', 51000   # chartplotter CDP config (debug-capture only)
GEN_VOTE_MIN = 5                # packets to vote before latching the generation


def imagery_diag_level(transmitting, ping_age, stale_after=3.0):
    """
    Return (level, message) for the imagery-stream diagnostic.

    Staleness only matters while transmitting -- in standby no pings are
    expected, so that is OK/idle, not an error.
    """
    if not transmitting:
        return DiagnosticStatus.OK, 'standby (no imagery expected)'
    if ping_age is None:
        return DiagnosticStatus.ERROR, 'transmitting but no imagery received'
    if ping_age > stale_after:
        return DiagnosticStatus.ERROR, f'transmitting but imagery stale ({ping_age:.1f}s)'
    return DiagnosticStatus.OK, f'receiving (last ping {ping_age:.1f}s ago)'


SIDES = ('port', 'stbd', 'down')
# Per-channel TF frame suffix (matches the topic names). Each transducer is a
# separate physical beam with its own pose, so it gets its own frame; the
# URDF/TF tree orients it (side + downward tilt). Mounting -- including a
# non-traditional/backwards install -- lives entirely in TF, never here.
FRAME_SUFFIX = {'port': 'port', 'stbd': 'starboard', 'down': 'down'}


def build_nadir_range(depth_m, frame_id, stamp, field_of_view, max_range):
    """
    Build a ``sensor_msgs/Range`` for a nadir bottom-range reading.

    Per ``sensor_msgs/Range`` the range is measured along the **+X axis** of
    ``frame_id``, so ``frame_id`` must be a dedicated nadir frame whose +X points
    down -- NOT the water-column ``_down`` frame, which is Z-down (the marine
    convention the down-look ``RawSonarImage`` uses; see ``docs/gcv_protocol.md``).
    ``min_range`` is 0 so a genuinely shallow reading is not flagged invalid;
    ``max_range`` is the ping's own observable window -- the caller passes the
    down-look sub-header v2 water-column extent, NOT a fixed configured bound.
    ``stamp`` is the ping receive time (the imagery stream carries no transmit
    clock).
    """
    msg = Range()
    msg.header.stamp = stamp
    msg.header.frame_id = frame_id
    msg.radiation_type = Range.ULTRASOUND
    msg.field_of_view = float(field_of_view)
    msg.min_range = 0.0
    msg.max_range = float(max_range)
    msg.range = float(depth_m)
    return msg


def transmit_state_after(commanded_on, send_ok, prior):
    """
    Return the transmit state to record after issuing a command.

    A failed OFF must not be recorded as OFF: the sonar may still be pinging,
    so we stay True and retry.  A successful command records the commanded
    state.  A failed ON keeps the ``prior`` state rather than asserting a state
    the command never achieved.
    """
    if not commanded_on:
        return not send_ok
    return True if send_ok else prior


def range_in_bounds(meters, range_min, range_max):
    """Return whether a requested range (m) is within the configured limits."""
    return range_min <= meters <= range_max


# Plausible surface-water-temperature window (deg C). A finite-but-absurd
# float from a corrupt telemetry frame (decode already drops non-finite) is
# gated out before publishing, mirroring the nadir-range validity gate -- a
# per-cycle gap is honest, a wild value is noise downstream.
WATER_TEMP_MIN_C, WATER_TEMP_MAX_C = -5.0, 50.0


def temperature_plausible(temp_c):
    """Return whether a water-temperature reading (deg C) is within sane bounds."""
    return WATER_TEMP_MIN_C <= temp_c <= WATER_TEMP_MAX_C


def temperature_publish_due(temp_c, last_c, elapsed_s, heartbeat_s=2.0):
    """
    Whether a water-temperature reading should be published.

    The d807 telemetry marker repeats the same value on each channel of a
    ~0.7 Hz triplet, so a changed value publishes immediately (collapsing the
    triplet to one message), and an unchanged value publishes on the first
    reading whose arrival is at least ``heartbeat_s`` after the last publish so
    the topic still ticks for consumers.  Because this is evaluated only when a
    frame arrives (~every 1.4 s for a steady reading), the effective steady-state
    cadence is the next triplet past ``heartbeat_s``, not exactly ``heartbeat_s``.
    ``last_c`` is None before the first reading (always due); ``elapsed_s`` is the
    time since the last publish (``inf`` for the first reading).
    """
    if last_c is None or temp_c != last_c:
        return True
    return elapsed_s >= heartbeat_s


class GarminSidescanNode(Node):
    """Driver node: GCV imagery in, RawSonarImage out, transmit on command."""

    def __init__(self):
        super().__init__('garmin_sidescan')

        # network / device
        self.declare_parameter('gcv_ip', '172.16.3.0')        # GCV-10 = 172.16.3.196
        self.declare_parameter('control_port', 50227)
        self.declare_parameter('mcast_group', '239.254.2.1')
        self.declare_parameter('mcast_port', 50220)
        self.declare_parameter('iface_ip', '')                 # local NIC for the join
        self.declare_parameter('filter_src', True)
        self.declare_parameter('frame_id', 'garmin_sidescan')

        # channel map (GCV-20: 0=port 1=stbd 2=down; GCV-10 data: port=[3] stbd=[1])
        self.declare_parameter('port_channels', [0])
        self.declare_parameter('stbd_channels', [1])
        self.declare_parameter('down_channels', [2])

        # frequency is NOT carried in the imagery stream; set per transducer or
        # leave 0.0 = unavailable (RawSonarImage/PingInfo convention).
        self.declare_parameter('freq_port_hz', 0.0)
        self.declare_parameter('freq_stbd_hz', 0.0)
        self.declare_parameter('freq_down_hz', 0.0)
        self.declare_parameter('sample_rate_hz', 0.0)

        # Nadir bottom range (sensor_msgs/Range from the per-ping sub-header
        # v1 varint, M3-validated). The Range beam axis is +X, so this needs
        # its own frame whose +X points down -- distinct from the Z-down
        # water-column _down frame. Empty -> derive '<frame_id>_nadir'; the
        # platform URDF supplies the TF.
        self.declare_parameter('nadir_frame_id', '')
        self.declare_parameter('nadir_beam_width_rad', 0.0)   # Range.field_of_view

        # device generation. 'auto' detects from the render-layer structure
        # (GCV-10 = 3 layers, GCV-20 <= 2; see decode.generation_from_layers),
        # which is range-independent and picks the right per-packet echo
        # extractor on every packet. 'gcv20'/'gcv10' force it. A wrong choice
        # silently yields a wrong-layer (gibberish) image -- no crash -- so
        # auto-detect is the default and a mismatch is warned.
        self.declare_parameter('device', 'auto')
        # Debug: when true, publish every raw UDP payload on ~/debug/raw
        # (std_msgs/UInt8MultiArray) so `ros2 bag record` captures fully
        # re-decodable pings. Dynamically settable at runtime.
        self.declare_parameter('debug_raw', False)

        # transmit / safety
        self.declare_parameter('transmit_on_startup', False)
        self.declare_parameter('startup_off_repeats', 3)

        # operator control set (radar-style; rendered by CAMP). Declare the range
        # bounds first so range_m can carry a FloatingPointRange descriptor built
        # from them for the marine_control panel.
        self.declare_parameter('range_min_m', 1.0)
        self.declare_parameter('range_max_m', 60.0)
        # range_m default 0.0 = "do not command a range at startup". The
        # descriptor upper bound is range_max_m; the lower bound stays 0.0 so the
        # default is valid (sub-range_min_m values are rejected by _on_param_set,
        # which enforces range_min_m). step 0.0 = continuous.
        range_max = float(self.get_parameter('range_max_m').value)
        self.declare_parameter(
            'range_m', 0.0,
            ParameterDescriptor(
                description=('Sonar display range in metres (0 = leave the device '
                             'default at startup; effective minimum is range_min_m).'),
                floating_point_range=[
                    FloatingPointRange(from_value=0.0, to_value=range_max, step=0.0)]))
        # transmit on/off as a bridgeable marine_control. Holds ACTUAL transmit
        # state, reconciled by a timer so the operator panel never lies.
        self.declare_parameter(
            'transmit', False,
            ParameterDescriptor(description='Sonar transmit on/off.'))
        # TVG / interference frames are GCV-10-derived and unverified on the
        # GCV-20 (TVG is display-side there); expose them but allow opting out.
        self.declare_parameter('expose_gcv10_controls', True)

        # Sound speed (m/s) used to convert the device's per-ping display range
        # into the published RawSonarImage scale (sample_rate) and stamped into
        # ping_info.sound_speed. This is the DEVICE's assumed sound speed for
        # scale reconstruction, not a live measurement: the GCV reports range in
        # metres already, so any consistent value round-trips the geometry, and
        # the device exposes no water-type / sound-speed setting (it uses a fixed
        # internal nominal). A consumer that re-applies its own sound-speed
        # correction must match this value. Default 1500 m/s.
        self.declare_parameter('sound_speed', 1500.0)

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
        for ch in self._p('down_channels'):
            self._chan_side[ch] = 'down'
        self._freq = {
            'port': float(self._p('freq_port_hz')),
            'stbd': float(self._p('freq_stbd_hz')),
            'down': float(self._p('freq_down_hz')),
        }
        self._sample_rate = float(self._p('sample_rate_hz'))
        self._nadir_frame_id = self._p('nadir_frame_id') or f'{self._frame_id}_nadir'
        self._nadir_fov = float(self._p('nadir_beam_width_rad'))
        self._chan_beamtype = {}      # ch -> 'sidescan'|'down' from pl[8]
        self._device = str(self._p('device')).lower()
        if self._device not in ('auto', 'gcv20', 'gcv10'):
            self.get_logger().warn(
                f"device='{self._device}' is not auto/gcv20/gcv10; "
                'falling back to auto-detect')
            self._device = 'auto'
        self._debug_raw = bool(self._p('debug_raw'))
        self._sound_speed = float(self._p('sound_speed'))

        # state
        self._tx_lock = threading.Lock()      # guards transmit state changes
        self._send_lock = threading.Lock()    # serializes TCP command sends
        self._transmitting = False
        # True while _reconcile_transmit_param mirrors actual state into the
        # `transmit` param, so _on_param_set skips re-issuing a transmit command.
        self._tx_sync = False
        self._control_server = None
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
        # The per-packet sample extractor depends on the device generation, so
        # the assembler is built lazily once the generation is known (immediately
        # for an explicit device; after geometry detection for 'auto').
        self._assembler = None
        self._detected_gen = None
        self._gen_votes = {}          # structural generation votes (see _detect_generation)
        self._geom_warned = False
        # Water-temperature dedup: the d807 telemetry marker repeats the same
        # reading on each channel of a triplet; publish one per distinct value,
        # with a heartbeat so a steady reading still ticks (see _emit_temperature).
        self._last_temp_c = None
        self._last_temp_t = None
        # Sample format is per-channel, carried on each ScanLine (`bits`) and
        # applied in _make_sonar_msg -- the GCV-10 side-scan is 8-bit while its
        # water-column and all GCV-20 channels are 16-bit, so there is no single
        # device-wide dtype.
        self._running = True
        # diagnostics state
        self._last_ping_t = None          # monotonic time of last emitted ping
        self._device_transmitting = None  # tx flag from the :50050 status frame
        self._last_status_t = None        # monotonic time of last status frame

        # publishers
        img_qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT)
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._pub_sonar = {
            'port': self.create_publisher(RawSonarImage, '~/sonar_image_port', img_qos),
            'stbd': self.create_publisher(RawSonarImage, '~/sonar_image_starboard', img_qos),
            'down': self.create_publisher(RawSonarImage, '~/sonar_image_down', img_qos),
        }
        # Nadir bottom range decoded from the down-look sub-header (per-ping
        # v1 varint, M3-validated to ~1%), as a downward sensor_msgs/Range
        # (see build_nadir_range / the _nadir frame). Per-ping sensor data, so
        # the imagery QoS, not latched.
        self._pub_depth = self.create_publisher(Range, '~/nadir_depth', img_qos)
        # Transducer surface water temperature decoded from the d807 telemetry
        # marker (per-channel triplet ~0.7 Hz; deduplicated to one reading per
        # cycle). Validated against an AML CTD cast + the boat's SVS (issue #37).
        self._pub_temperature = self.create_publisher(
            Temperature, '~/water_temperature', img_qos)
        # Raw-payload debug capture (only published when debug_raw is true) --
        # one topic per GCV multicast stream so a single bag is fully
        # re-decodable offline (imagery + status + chartplotter config).
        u8 = UInt8MultiArray
        self._pub_raw = self.create_publisher(u8, '~/debug/raw', img_qos)
        self._pub_raw_status = self.create_publisher(u8, '~/debug/raw_status', img_qos)
        self._pub_raw_config = self.create_publisher(u8, '~/debug/raw_config', img_qos)
        self._pub_diag = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self._pub_tx = self.create_publisher(Bool, '~/transmitting', latched)
        self._pub_status = self.create_publisher(String, '~/status', latched)
        # Control set: volatile depth-10, NOT latched -- mirrors simrad_halo_radar
        # (the rqt control panel + udp_bridge are built around the radar's model).
        # transient_local does not cross the udp_bridge, and a volatile subscriber
        # never sees a latched sample; the set is instead re-sent on a heartbeat
        # (see _publish_status) so a late / bridged subscriber always populates.
        self._pub_state = self.create_publisher(RadarControlSet, '~/state', 10)

        self.create_service(SetBool, '~/set_transmit', self._on_set_transmit)
        self.create_subscription(RadarControlValue, '~/change_state',
                                 self._on_control_value, 10)

        self.add_on_set_parameters_callback(self._on_param_set)

        # marine_control device panel (bridgeable; rendered by rqt_marine_control).
        # transmit + range for now; TVG/interference await enum support in the lib.
        # Created before the timers so the reconcile timer can mirror the transmit param.
        self._control_server = ControlServer(self)
        self._control_server.bind_parameter('transmit', group='sonar')
        self._control_server.bind_parameter('range_m', units='m', group='sonar')

        self._rx_thread = threading.Thread(target=self._rx_loop, name='gcv_rx', daemon=True)
        self._rx_thread.start()
        # Auxiliary listeners: status feeds diagnostics (device tx flag) and is
        # captured under debug_raw; config is debug-capture only.
        threading.Thread(
            target=self._aux_loop, name='gcv_status', daemon=True,
            args=(STATUS_GROUP, STATUS_PORT, self._pub_raw_status, self._on_status)).start()
        threading.Thread(
            target=self._aux_loop, name='gcv_config', daemon=True,
            args=(CONFIG_GROUP, CONFIG_PORT, self._pub_raw_config, None)).start()
        self.create_timer(0.5, self._reconcile_transmit_param)
        self.create_timer(2.0, self._publish_status)
        self.create_timer(1.0, self._publish_diagnostics)

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
            # status reflects reality rather than reporting a false OFF.
            self._transmitting = not ok
        self._publish_tx_state()
        if ok:
            self.get_logger().info('startup: transmit asserted OFF')
        else:
            self.get_logger().error(
                'startup: could not assert transmit OFF (GCV unreachable?); '
                'assuming sonar may be pinging')
        if range_m > 0:
            if self._send(build_range_cmd(range_m)):
                self._controls['range'] = f'{range_m:.1f}'
                self.get_logger().info(f'startup: range set to {range_m} m')
            else:
                self.get_logger().error(f'startup: range command ({range_m} m) failed to send')
        if want_on:
            self._set_transmit(True, 'startup (transmit_on_startup)')

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
            # A failed OFF keeps _transmitting True so status reflects reality
            # (the sonar may still be pinging) and we retry; a failed ON keeps
            # the prior state (see transmit_state_after).
            self._transmitting = transmit_state_after(on, ok, prior=self._transmitting)
        if not on and not ok:
            self.get_logger().error(
                f'transmit OFF command FAILED ({reason}); sonar may still be '
                'pinging - will retry')
        else:
            self.get_logger().info(
                f'transmit {"ON" if self._transmitting else "OFF"} ({reason})')
        self._publish_tx_state()

    def _publish_tx_state(self):
        self._pub_tx.publish(Bool(data=bool(self._transmitting)))
        # keep the operator-control mirror in sync with actual transmit state
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
            self._set_transmit(True, 'request')
            if self._transmitting:
                return True, 'transmitting'
            return False, 'transmit ON command failed to send'
        self._set_transmit(False, 'request')
        if self._transmitting:
            return False, 'transmit OFF command failed to send; sonar may still be pinging'
        return True, 'transmit off'

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

    def _reconcile_transmit_param(self):
        """
        Mirror actual transmit state into the `transmit` parameter.

        Runs on the executor thread (the reconcile timer) so a startup-thread-
        driven transmit change reaches the ControlServer echo without a
        cross-thread set_parameters. The _tx_sync guard keeps _on_param_set
        from re-issuing a transmit command for this mirror write.
        """
        if not self.has_parameter('transmit'):
            return
        # Snapshot under _tx_lock -- the startup daemon thread also mutates
        # _transmitting -- for a consistent read, matching the file's discipline.
        with self._tx_lock:
            actual = self._transmitting
        if bool(self.get_parameter('transmit').value) == actual:
            return
        self._tx_sync = True
        try:
            self.set_parameters([Parameter('transmit', Parameter.Type.BOOL, actual)])
        finally:
            self._tx_sync = False
        if self._control_server is not None:
            self._control_server.publish_state()

    # ----- imagery receive + decode -----------------------------------------
    def _open_mcast(self, group, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', port))
        iface = socket.inet_aton(self._iface_ip) if self._iface_ip \
            else socket.inet_aton('0.0.0.0')
        mreq = socket.inet_aton(group) + iface
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1.0)
        return sock

    def _aux_loop(self, group, port, raw_pub, on_payload):
        """
        Listen to an auxiliary GCV multicast stream (status / config).

        Parses each payload via ``on_payload`` (status tx flag), and republishes
        the raw bytes on ``raw_pub`` when ``debug_raw`` is set so the stream is
        captured for offline decode. Mirrors ``_rx_loop``'s reconnect handling.
        """
        sock = None
        while self._running:
            if sock is None:
                try:
                    sock = self._open_mcast(group, port)
                except OSError:
                    time.sleep(2.0)
                    continue
            try:
                payload, _addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                sock.close()
                sock = None
                continue
            if on_payload is not None:
                on_payload(payload)
            if self._debug_raw:
                raw_pub.publish(UInt8MultiArray(data=payload))
        if sock is not None:
            sock.close()

    def _on_status(self, payload):
        tx = status_transmitting(payload)
        if tx is not None:
            self._device_transmitting = tx
            self._last_status_t = time.monotonic()

    def _rx_loop(self):
        sock = None
        while self._running:
            if sock is None:
                try:
                    sock = self._open_mcast(self._group, self._mport)
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
            if self._debug_raw:
                # Full UDP payload (magic + channel + all layers) so the bag is
                # re-decodable offline. Published as-is; bag timestamps give timing.
                self._pub_raw.publish(UInt8MultiArray(data=payload))
            now = self.get_clock().now()
            # Water-temperature telemetry has no dependency on the imagery
            # assembler, so decode it before the generation-detect gate below --
            # otherwise the topic stays silent through auto-detect warmup.
            temp_c = marker_temperature_c(payload)
            if temp_c is not None:
                self._emit_temperature(temp_c, now)
            detected = self._detect_generation(payload)
            self._classify_beam(payload)
            if self._assembler is None:
                gen = self._device if self._device in ('gcv20', 'gcv10') else detected
                if gen is None:
                    continue                       # auto: not enough packets yet
                self._assembler = self._make_assembler(gen)
            elif (self._device in ('gcv20', 'gcv10') and detected
                  and detected != self._device and not self._geom_warned):
                self._geom_warned = True
                self.get_logger().warn(
                    f'device={self._device} but packet geometry looks like '
                    f'{detected}; imagery decode is likely wrong')
            for line in self._assembler.feed(payload, now):
                self._emit_ping(line)
        if self._assembler is not None:
            for line in self._assembler.flush():
                self._emit_ping(line)
        if sock is not None:
            sock.close()

    def _make_assembler(self, gen):
        # Extraction is per-packet: PingAssembler self-selects dark_layer (8-bit)
        # vs echo_layer (16-bit) from each packet's render-layer structure, so
        # the assembler is generation-agnostic. This is what lets the GCV-10
        # water-column (a 16-bit echo layer) decode even though the device
        # latches as GCV-10 -- a single device-wide extractor blanked it.
        self.get_logger().info(
            f'imagery decode: device {gen}; per-channel extraction '
            '(GCV-10 side=8-bit dark, water-column/GCV-20=16-bit echo)')
        return PingAssembler()

    def _detect_generation(self, payload):
        """
        Return the latched device generation, or None until enough packets vote.

        Uses the **structural** signal (:func:`generation_from_layers`: render-layer
        count) -- range-independent, present on every sample packet, so a deep
        GCV-20 is recognised immediately. (byte 13, the old "gen tag", is actually
        the range bracket and mislabels a deep/shallow GCV-20.) Votes are
        accumulated over the first :data:`GEN_VOTE_MIN` classifiable packets and
        the majority latched, so one miscounted packet can't pick the wrong
        extractor for the session.
        """
        if self._detected_gen is not None:
            return self._detected_gen
        gen = generation_from_layers(payload)
        if gen is not None:
            self._gen_votes[gen] = self._gen_votes.get(gen, 0) + 1
            if sum(self._gen_votes.values()) >= GEN_VOTE_MIN:
                self._detected_gen = max(self._gen_votes, key=self._gen_votes.get)
        return self._detected_gen

    def _classify_beam(self, payload):
        """
        Record each channel's beam type and warn on a channel-map mismatch.

        The intrinsic beam type (down-look vs side-scan, from the render-layer byte)
        of the down-look "water column" beam is identifiable from the stream
        itself; this catches a mis-configured channel map (e.g. a channel
        routed to a side-scan topic that is actually the down-look beam) without
        relying on the unit-specific channel numbers.
        """
        if payload[:2] != EB07 or len(payload) <= MIN_DATA_LEN:
            return
        ch = payload[CHANNEL_OFFSET]
        if ch in self._chan_beamtype:
            return
        observed = 'down' if is_water_column(payload) else 'sidescan'
        self._chan_beamtype[ch] = observed
        side = self._chan_side.get(ch)
        if side is None:
            return
        configured = 'down' if side == 'down' else 'sidescan'
        if observed != configured:
            self.get_logger().warn(
                f'channel {ch} is mapped to {side} ({configured}) but the '
                f'stream reports {observed} (render-layer byte); check the '
                f'port/stbd/down channel map')

    def _emit_ping(self, line):
        side = self._chan_side.get(line.channel)
        if side is None or not line.samples:
            return
        self._ping_count[side] += 1
        self._last_ping_t = time.monotonic()
        self._pub_sonar[side].publish(
            self._make_sonar_msg(side, line.samples, line.stamp, line.subheader,
                                 line.bits))
        # Nadir bottom range: the per-ping sub-header v1 varint. It is shared
        # across channels (the boat's depth), but published once per ping from
        # the down-look -- the beam that actually measures it. max_range is
        # the ping's own water-column extent (v2): the sensor's actual
        # observable window this ping, NOT the side-scan command clamp. A
        # parseable-but-implausible v1 (non-positive, or beyond the window)
        # is not published at all -- a per-ping gap is honest sensor output,
        # a spec-invalid Range (range > max_range) is just noise downstream.
        if side == 'down' and line.subheader:
            v1, v2 = line.subheader.bottom_range_m, line.subheader.display_range_m
            if 0.0 < v1 <= v2:
                self._pub_depth.publish(build_nadir_range(
                    v1, self._nadir_frame_id, line.stamp.to_msg(),
                    self._nadir_fov, v2))

    def _emit_temperature(self, temp_c, stamp):
        # A corrupt frame can decode to a finite-but-absurd value (decode drops
        # non-finite already); gate it out rather than publish noise.
        if not temperature_plausible(temp_c):
            return
        # Collapse a per-channel triplet to one message; heartbeat a steady
        # value (see temperature_publish_due). On a backward clock jump (sim
        # reset / bag loop) elapsed goes negative: a changed value still
        # publishes, a steady one resumes heartbeating once the clock recovers.
        elapsed = (float('inf') if self._last_temp_t is None
                   else (stamp - self._last_temp_t).nanoseconds * 1e-9)
        if not temperature_publish_due(temp_c, self._last_temp_c, elapsed):
            return
        self._last_temp_c = temp_c
        self._last_temp_t = stamp
        msg = Temperature()
        msg.header.stamp = stamp.to_msg()
        # The transducer's own sensor -- tag it with the base sensor frame.
        msg.header.frame_id = self._frame_id
        msg.temperature = float(temp_c)
        msg.variance = 0.0          # unknown (0 = "variance unknown" per the spec)
        self._pub_temperature.publish(msg)

    def _make_sonar_msg(self, side, samples, stamp, sub=None, bits=16):
        msg = RawSonarImage()
        msg.header.stamp = stamp.to_msg()
        # Per-channel frame so TF orients each transducer (see FRAME_SUFFIX).
        msg.header.frame_id = f'{self._frame_id}_{FRAME_SUFFIX[side]}'
        msg.ping_info.frequency = self._freq[side]
        sv = self._sound_speed
        msg.ping_info.sound_speed = sv
        # The GCV frames each ping as a fixed GRID_BINS-sample line spanning
        # [0, display_range] and gates the near-field by delivering only the
        # LAST `bins` of that grid. So derive sample_rate from the full grid and
        # expose the omitted head count as sample0: a consumer then recovers a
        # delivered sample j at range = sv*(sample0 + j)/(2*rate), i.e. the data
        # starts at the near-field offset, not at range 0. (Publishing sample0=0
        # mis-scales every sample, by ~20% of range at short range.)
        # The range source is the ping's OWN sub-header v2 (per channel, tracks
        # hardware auto-range), falling back to the commanded-range mirror and
        # then the sample_rate_hz parameter -- see decode.derive_sample_rate.
        # The commanded fallback applies to the side-scan only: the down-look's
        # range is its auto-ranged water-column extent, never the commanded
        # swath, so a wrong-but-confident ~2x scale must not be published --
        # better "unavailable" than wrong.
        bytes_per_sample = bits // 8
        bins = len(samples) // bytes_per_sample
        if bins > GRID_BINS:
            # The device is not expected to exceed the fixed grid; if it does,
            # the grid model is violated and the scale below is wrong for the
            # over-length tail (sample0 floors at 0 via max()). Loud + throttled
            # rather than silently publishing a confidently-wrong scale.
            self.get_logger().warn(
                f'ping has {bins} bins > GRID_BINS ({GRID_BINS}); fixed-grid '
                'scale assumption violated, published scale is unreliable',
                throttle_duration_sec=30.0)
        commanded = (0.0 if side == 'down'
                     else float(self._controls.get('range') or 0.0))
        msg.sample_rate = derive_sample_rate(
            sub, GRID_BINS, sv, commanded_range_m=commanded,
            fallback_rate=self._sample_rate)
        msg.samples_per_beam = bins
        # Near-field gate: the first (GRID_BINS - bins) grid samples are omitted,
        # so the delivered data starts at grid sample index sample0.
        msg.sample0 = max(0, GRID_BINS - bins)
        # rx_angles/tx_angles are the *steering* angle applied to the beam
        # (per the RawSonarImage spec) -- 0 for a fixed, unsteered single-beam
        # sidescan. The transducer's physical look direction (port out / stbd
        # out / down-look) is mounting, expressed by the per-channel frame_id +
        # the TF tree, not baked into these angles.
        msg.tx_delays = [0.0]
        msg.tx_angles = [0.0]
        msg.rx_angles = [0.0]
        msg.image.is_bigendian = False        # GCV samples are little-endian
        msg.image.dtype = (SonarImageData.DTYPE_UINT16 if bits == 16
                           else SonarImageData.DTYPE_UINT8)
        msg.image.beam_count = 1
        msg.image.data = bytes(samples)
        return msg

    def _publish_diagnostics(self):
        now = time.monotonic()
        ping_age = None if self._last_ping_t is None else now - self._last_ping_t

        # Imagery stream
        lvl, msg = imagery_diag_level(self._transmitting, ping_age)
        imagery = DiagnosticStatus(
            name='garmin_sidescan: imagery', hardware_id=self._gcv_ip,
            level=lvl, message=msg, values=[
                KeyValue(key='device', value=self._detected_gen or 'detecting'),
                KeyValue(key='last_ping_age_s',
                         value='n/a' if ping_age is None else f'{ping_age:.1f}'),
                KeyValue(key='pings_port_stbd_down',
                         value=f"{self._ping_count['port']}/{self._ping_count['stbd']}/"
                               f"{self._ping_count['down']}"),
            ])

        # Transmit state. WARN if the device-reported transmit state (from the
        # :50050 status frame) disagrees with what we commanded.
        dev_tx = self._device_transmitting
        mismatch = dev_tx is not None and dev_tx != self._transmitting
        tx_lvl = DiagnosticStatus.WARN if mismatch else DiagnosticStatus.OK
        tx_msg = (f'commanded {self._transmitting} but device reports {dev_tx}'
                  if mismatch
                  else ('transmitting' if self._transmitting else 'standby'))
        transmit = DiagnosticStatus(
            name='garmin_sidescan: transmit', hardware_id=self._gcv_ip,
            level=tx_lvl, message=tx_msg, values=[
                KeyValue(key='commanded_transmitting', value=str(self._transmitting)),
                KeyValue(key='device_transmitting',
                         value='unknown' if dev_tx is None else str(dev_tx)),
                KeyValue(key='sound_speed_mps', value=f'{self._sound_speed:.1f}'),
            ])

        arr = DiagnosticArray()
        arr.header.stamp = self.get_clock().now().to_msg()
        arr.status = [imagery, transmit]
        self._pub_diag.publish(arr)

    def _publish_status(self):
        self._pub_status.publish(String(data=(
            f'tx={"ON" if self._transmitting else "OFF"} '
            f'sound_speed={self._sound_speed:.1f} '
            f'pings(port/stbd/down)={self._ping_count["port"]}/'
            f'{self._ping_count["stbd"]}/{self._ping_count["down"]}')))
        # Re-publish the control set on the status timer so a late or udp-bridged
        # subscriber always populates: ~/state is volatile, so it never sees the
        # on-change publishes. The radar heartbeats this at 1 Hz; here it rides
        # the existing 2 s status timer.
        self._publish_control_set()

    def _on_param_set(self, params):
        # Validate the whole batch before applying ANY side effect. rclpy
        # accepts/rejects a set_parameters() call atomically on the single
        # returned result, so applying a side effect (sending the range command,
        # mirroring it to the UI) for one param and then rejecting the batch
        # because of a *different* param would desync the param store from the
        # hardware/UI. Side effects happen only once every param is acceptable.
        range_request = None
        transmit_request = None
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
            elif p.name == 'transmit':
                transmit_request = bool(p.value)  # applied below (or mirror-only)
            elif p.name == 'debug_raw':
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

        # Apply transmit last. ControlServer sets exactly one parameter per
        # change, so transmit and range never share a batch from the operator
        # path; applying it after range therefore can't break range's atomicity.
        # Skip the command when this set is our own reconcile write (_tx_sync) --
        # that only mirrors actual state into the param.
        if transmit_request is not None and not self._tx_sync:
            ok, reason = self._request_transmit(transmit_request)
            if not ok:
                # Not achieved (a failed send can leave the sonar possibly still
                # pinging): reject so the param stays at the actual transmit
                # state, not the request.
                return SetParametersResult(successful=False, reason=reason)

        for p in params:
            if p.name == 'debug_raw':
                self._debug_raw = bool(p.value)
                self.get_logger().info(
                    'debug_raw ON - publishing raw payloads on ~/debug/raw'
                    if self._debug_raw else 'debug_raw off')
        return SetParametersResult(successful=True)

    def destroy_node(self):
        """Stop the receive loop and assert transmit off on shutdown."""
        self._running = False
        # Retry the OFF; a single dropped frame on shutdown must not leave the
        # sonar pinging unattended. _send already swallows OSError -> bool.
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
