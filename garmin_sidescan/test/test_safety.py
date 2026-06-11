"""
Unit tests for the transmit-state safety rule.

The critical invariant: a transmit-OFF command whose TCP send FAILED must not
be recorded as OFF, or the watchdog would stop retrying and a dry transducer
could keep pinging while everything reports OFF.
"""
import types

from builtin_interfaces.msg import Time
from diagnostic_msgs.msg import DiagnosticStatus
from garmin_sidescan.node import (
    build_nadir_range,
    GarminSidescanNode,
    imagery_diag_level,
    range_in_bounds,
    transmit_state_after,
    watchdog_action,
)
from sensor_msgs.msg import Range


def test_build_nadir_range_maps_depth_to_downward_range():
    msg = build_nadir_range(14.58, 'gs_nadir', Time(sec=5, nanosec=0),
                            field_of_view=0.2, max_range=60.0)
    assert msg.header.frame_id == 'gs_nadir'        # dedicated +X-down frame
    assert msg.header.stamp.sec == 5                # receive-time stamp
    assert msg.radiation_type == Range.ULTRASOUND
    assert abs(msg.range - 14.58) < 1e-4            # depth -> range
    assert msg.min_range == 0.0                     # shallow not flagged invalid
    assert abs(msg.max_range - 60.0) < 1e-4
    assert abs(msg.field_of_view - 0.2) < 1e-4


def test_imagery_diag_level():
    ok, err = DiagnosticStatus.OK, DiagnosticStatus.ERROR
    assert imagery_diag_level(False, None)[0] == ok      # standby: no pings expected
    assert imagery_diag_level(True, None)[0] == err      # transmitting, never received
    assert imagery_diag_level(True, 10.0)[0] == err      # transmitting, stale
    assert imagery_diag_level(True, 0.5)[0] == ok        # transmitting, fresh


def test_successful_on_is_transmitting():
    assert transmit_state_after(commanded_on=True, send_ok=True, prior=False) is True


def test_failed_on_from_off_stays_off():
    # ON requested from a known-off state, send failed -> still off
    assert transmit_state_after(commanded_on=True, send_ok=False, prior=False) is False


def test_failed_on_while_possibly_pinging_stays_transmitting():
    # safety-critical: a prior failed OFF left prior=True (may still be pinging);
    # an auto-resume ON whose send also fails must NOT report OFF and disarm the
    # watchdog over a live, dry transducer.
    assert transmit_state_after(commanded_on=True, send_ok=False, prior=True) is True


def test_successful_off_is_not_transmitting():
    assert transmit_state_after(commanded_on=False, send_ok=True, prior=True) is False


def test_failed_off_stays_transmitting():
    # the safety-critical case: OFF send failed -> assume still pinging
    assert transmit_state_after(commanded_on=False, send_ok=False, prior=False) is True


# ----- watchdog decision (watchdog_action) -------------------------------

def _wd(**kw):
    base = {'safety_enabled': True, 'transmitting': True, 'has_sv_topic': True,
            'require_sv': True, 'sv_age': 0.0, 'sv_timeout': 5.0}
    base.update(kw)
    return watchdog_action(**base)


def test_watchdog_idle_when_safety_disabled():
    assert _wd(safety_enabled=False) == (False, '')


def test_watchdog_idle_when_not_transmitting():
    assert _wd(transmitting=False) == (False, '')


def test_watchdog_fresh_reading_no_action():
    assert _wd(sv_age=1.0) == (False, '')


def test_watchdog_stale_reading_stops():
    assert _wd(sv_age=9.0) == (True, 'stale')


def test_watchdog_never_received_stops():
    assert _wd(sv_age=None) == (True, 'stale')


def test_watchdog_no_source_with_require_sv_stops():
    # require_sv + no sound-speed source while (maybe) transmitting: the guard
    # refuses to start here, so the watchdog must stop rather than dry-ping.
    assert _wd(has_sv_topic=False, require_sv=True) == (True, 'no_source')


def test_watchdog_no_source_without_require_sv_leaves_transmit():
    # bench testing (require_sound_speed:=false): nothing to evaluate, no stop
    assert _wd(has_sv_topic=False, require_sv=False) == (False, '')


# ----- range bounds (range_in_bounds) ------------------------------------

def test_range_in_bounds_inclusive_edges():
    assert range_in_bounds(1.0, 1.0, 60.0)
    assert range_in_bounds(60.0, 1.0, 60.0)
    assert range_in_bounds(30.0, 1.0, 60.0)


def test_range_below_min_rejected():
    assert not range_in_bounds(0.5, 1.0, 60.0)


def test_range_above_max_rejected():
    assert not range_in_bounds(100.0, 1.0, 60.0)


# ----- _on_param_set batch atomicity (#19) -------------------------------

class _FakeLogger:
    """No-op logger so _on_param_set can run without a real node."""

    def warn(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass


class _FakeNode:
    """Minimal stand-in exposing only the attributes _on_param_set touches."""

    def __init__(self, send_ok=True, static_params=()):
        self._send_ok = send_ok
        self.sends = []
        self._static = set(static_params)
        self._range_min = 1.0
        self._range_max = 60.0
        self._controls = {}
        self._safety_enabled = True
        self.publishes = 0

    def get_logger(self):
        return _FakeLogger()

    def _send(self, data):
        self.sends.append(data)
        return self._send_ok

    def has_parameter(self, name):
        return name in self._static

    def _publish_control_set(self):
        self.publishes += 1


def _param(name, value):
    return types.SimpleNamespace(name=name, value=value)


def _on_param_set(node, params):
    return GarminSidescanNode._on_param_set(node, params)


def test_valid_range_alone_sends_and_succeeds():
    node = _FakeNode()
    result = _on_param_set(node, [_param('range_m', 30.0)])
    assert result.successful is True
    assert len(node.sends) == 1
    assert node._controls['range'] == '30.0'


def test_batch_rejects_static_param_without_sending_range():
    # The #19 case: a valid range_m bundled with a startup-static param must be
    # rejected atomically -- the range command must NOT have been sent and the
    # UI mirror must NOT have been updated, or the param store desyncs from the
    # hardware/UI.
    node = _FakeNode(static_params=('sv_min',))
    result = _on_param_set(
        node, [_param('range_m', 30.0), _param('sv_min', 1400.0)])
    assert result.successful is False
    assert node.sends == []
    assert 'range' not in node._controls


def test_batch_rejection_independent_of_param_order():
    # Same as above but static param first: the validate-all pre-pass must catch
    # it regardless of ordering, still without sending the range command.
    node = _FakeNode(static_params=('sv_min',))
    result = _on_param_set(
        node, [_param('sv_min', 1400.0), _param('range_m', 30.0)])
    assert result.successful is False
    assert node.sends == []


def test_range_send_failure_rejects_without_mirroring():
    node = _FakeNode(send_ok=False)
    result = _on_param_set(node, [_param('range_m', 30.0)])
    assert result.successful is False
    assert len(node.sends) == 1          # attempted
    assert 'range' not in node._controls  # but not mirrored on failure


# ----- device auto-detect by sub-header tag byte -------------------------

def _img_packet(tag, length=40):
    # eb07 + 4-byte len + sub-header (value-width tag at offset 13), padded
    head = bytes([0xeb, 0x07, 0, 0]) + bytes(4) + bytes([0x0e, 1, 3, 9, 0, tag])
    return head + bytes(max(0, length - len(head)))


def _detect(node, payload):
    return GarminSidescanNode._detect_generation(node, payload)


def test_detect_generation_by_tag_byte():
    node = types.SimpleNamespace(_detected_gen=None)
    assert _detect(node, _img_packet(0x12)) == 'gcv20'
    assert _detect(node, _img_packet(0x11)) == 'gcv20'   # decide-once: stays gcv20
    node = types.SimpleNamespace(_detected_gen=None)
    assert _detect(node, _img_packet(0x11)) == 'gcv10'


def test_detect_generation_undecided_cases():
    node = types.SimpleNamespace(_detected_gen=None)
    assert _detect(node, _img_packet(0x99)) is None        # unknown tag
    assert _detect(node, b'\xeb\x07' + bytes(8)) is None    # too short for tag
    assert _detect(node, b'\xd8\x07' + bytes(40)) is None   # not an eb07 packet
    assert node._detected_gen is None                       # still undecided


# ----- intrinsic beam-type classification (down-look vs side-scan) -------

def _img_layer(layer, ch=0):
    return bytes([0xeb, 0x07, 0, 0]) + bytes(4) + bytes([layer, 1, 3, 9, ch]) + bytes(24)


class _RecLogger:
    """Capturing logger stub so a test can assert on warnings."""

    def __init__(self):
        self.warns = []

    def warn(self, m):
        self.warns.append(m)

    def error(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass


def _classify_node(chan_side):
    log = _RecLogger()
    node = types.SimpleNamespace(_chan_beamtype={}, _chan_side=chan_side,
                                 get_logger=lambda: log)
    return node, log


def test_classify_beam_warns_on_down_mismatch():
    # channel 0 mapped to 'port' but the stream's layer byte is down-look (0x0d)
    node, log = _classify_node({0: 'port'})
    GarminSidescanNode._classify_beam(node, _img_layer(0x0d, ch=0))
    assert node._chan_beamtype[0] == 'down'
    assert len(log.warns) == 1 and 'channel 0' in log.warns[0]


def test_classify_beam_silent_when_consistent():
    # down channel carrying the down-look layer; sidescan channel carrying side-scan
    node, log = _classify_node({2: 'down', 0: 'port'})
    GarminSidescanNode._classify_beam(node, _img_layer(0x0d, ch=2))
    GarminSidescanNode._classify_beam(node, _img_layer(0x0e, ch=0))
    assert node._chan_beamtype == {2: 'down', 0: 'sidescan'}
    assert log.warns == []


def test_status_heartbeat_republishes_control_set():
    # Regression for #30: ~/state is volatile (not latched), so the control set
    # must be re-published on the periodic status heartbeat -- not only on change
    # -- or a late / udp-bridged rqt subscriber never populates the control panel.
    node = _FakeNode()
    node._transmitting = False
    node._require_sv = True
    node._safety_latched = False
    node._last_sv_value = 1500.0
    node._ping_count = {'port': 0, 'stbd': 0, 'down': 0}
    node._sv_age = lambda: None
    node._pub_status = types.SimpleNamespace(publish=lambda msg: None)
    GarminSidescanNode._publish_status(node)
    assert node.publishes == 1
