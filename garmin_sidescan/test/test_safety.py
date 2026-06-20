"""
Unit tests for the transmit-state rule.

The critical invariant: a transmit-OFF command whose TCP send FAILED must not
be recorded as OFF, or the sonar could keep pinging while everything reports OFF.
"""
import threading
import types

from diagnostic_msgs.msg import DiagnosticStatus
from garmin_sidescan.decode import FH, SH
from garmin_sidescan.node import (
    build_nadir_range,
    GarminSidescanNode,
    GEN_VOTE_MIN,
    imagery_diag_level,
    range_in_bounds,
    temperature_plausible,
    temperature_publish_due,
    transmit_state_after,
)


def test_imagery_diag_level():
    ok, err = DiagnosticStatus.OK, DiagnosticStatus.ERROR
    assert imagery_diag_level(False, None)[0] == ok      # standby: no pings expected
    assert imagery_diag_level(True, None)[0] == err      # transmitting, never received
    assert imagery_diag_level(True, 10.0)[0] == err      # transmitting, stale
    assert imagery_diag_level(True, 0.5)[0] == ok        # transmitting, fresh


def test_temperature_plausible():
    assert temperature_plausible(15.5) is True
    assert temperature_plausible(28.9) is True
    assert temperature_plausible(-5.0) is True       # boundary inclusive
    assert temperature_plausible(50.0) is True
    assert temperature_plausible(-40.0) is False      # corrupt-frame garbage
    assert temperature_plausible(1e6) is False


def test_temperature_publish_due():
    # first reading always publishes
    assert temperature_publish_due(15.5, None, 0.0) is True
    # a triplet of identical values collapses to one (the repeats are suppressed)
    assert temperature_publish_due(15.5, 15.5, 0.01) is False
    # a changed value publishes immediately
    assert temperature_publish_due(15.6, 15.5, 0.01) is True
    # a steady value heartbeats after the interval
    assert temperature_publish_due(15.5, 15.5, 2.5) is True
    assert temperature_publish_due(15.5, 15.5, 2.0) is True   # boundary inclusive


def test_successful_on_is_transmitting():
    assert transmit_state_after(commanded_on=True, send_ok=True, prior=False) is True


def test_failed_on_from_off_stays_off():
    # ON requested from a known-off state, send failed -> still off
    assert transmit_state_after(commanded_on=True, send_ok=False, prior=False) is False


def test_failed_on_while_possibly_pinging_stays_transmitting():
    # a prior failed OFF left prior=True (may still be pinging); an ON whose send
    # also fails must NOT report OFF over a sonar that may still be transmitting.
    assert transmit_state_after(commanded_on=True, send_ok=False, prior=True) is True


def test_successful_off_is_not_transmitting():
    assert transmit_state_after(commanded_on=False, send_ok=True, prior=True) is False


def test_failed_off_stays_transmitting():
    # the safety-critical case: OFF send failed -> assume still pinging
    assert transmit_state_after(commanded_on=False, send_ok=False, prior=False) is True


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

    def __init__(self, send_ok=True, static_params=(), tx_achieves=True):
        self._send_ok = send_ok
        self.sends = []
        self._static = set(static_params)
        self._range_min = 1.0
        self._range_max = 60.0
        self._controls = {}
        self.publishes = 0
        # transmit-control adoption
        self._tx_sync = False
        self._transmitting = False
        self._tx_achieves = tx_achieves   # does _request_transmit reach the goal?
        self._tx_force = None             # (ok, transmitting_after) override
        self.transmit_requests = []

    def get_logger(self):
        return _FakeLogger()

    def _send(self, data):
        self.sends.append(data)
        return self._send_ok

    def has_parameter(self, name):
        return name in self._static

    def _publish_control_set(self):
        self.publishes += 1

    def _request_transmit(self, on):
        self.transmit_requests.append(on)
        if self._tx_force is not None:
            ok, self._transmitting = self._tx_force
            return ok, 'forced'
        if self._tx_achieves:
            self._transmitting = on
            return True, 'ok'
        return False, 'refused'   # not achieved: actual state unchanged


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
    node = _FakeNode(static_params=('freq_port_hz',))
    result = _on_param_set(
        node, [_param('range_m', 30.0), _param('freq_port_hz', 800000.0)])
    assert result.successful is False
    assert node.sends == []
    assert 'range' not in node._controls


def test_batch_rejection_independent_of_param_order():
    # Same as above but static param first: the validate-all pre-pass must catch
    # it regardless of ordering, still without sending the range command.
    node = _FakeNode(static_params=('freq_port_hz',))
    result = _on_param_set(
        node, [_param('freq_port_hz', 800000.0), _param('range_m', 30.0)])
    assert result.successful is False
    assert node.sends == []


def test_range_send_failure_rejects_without_mirroring():
    node = _FakeNode(send_ok=False)
    result = _on_param_set(node, [_param('range_m', 30.0)])
    assert result.successful is False
    assert len(node.sends) == 1          # attempted
    assert 'range' not in node._controls  # but not mirrored on failure


# ----- transmit-control adoption (marine_control) ------------------------

def test_transmit_request_applied_when_achieved():
    node = _FakeNode()
    result = _on_param_set(node, [_param('transmit', True)])
    assert result.successful is True
    assert node.transmit_requests == [True]
    assert node._transmitting is True


def test_transmit_request_rejected_when_not_achieved():
    # The ON request isn't achieved (e.g. send failed): reject the set so the
    # param stays at the actual (off) transmit state rather than falsely on.
    node = _FakeNode(tx_achieves=False)
    result = _on_param_set(node, [_param('transmit', True)])
    assert result.successful is False
    assert node.transmit_requests == [True]
    assert node._transmitting is False


def test_transmit_off_applied_when_achieved():
    node = _FakeNode()
    node._transmitting = True
    result = _on_param_set(node, [_param('transmit', False)])
    assert result.successful is True
    assert node.transmit_requests == [False]
    assert node._transmitting is False


def test_transmit_off_send_failure_rejected():
    # Safety-critical branch: an OFF whose send failed leaves the sonar possibly
    # still pinging (_transmitting stays True). _request_transmit returns ok=False
    # there, so the set must be REJECTED -- the param must not claim 'off'.
    node = _FakeNode()
    node._transmitting = True
    node._tx_force = (False, True)   # ok=False, still transmitting
    result = _on_param_set(node, [_param('transmit', False)])
    assert result.successful is False
    assert node.transmit_requests == [False]
    assert node._transmitting is True


def test_transmit_sync_write_skips_command():
    # A reconcile (mirror) write must NOT re-issue a transmit command.
    node = _FakeNode()
    node._tx_sync = True
    result = _on_param_set(node, [_param('transmit', True)])
    assert result.successful is True
    assert node.transmit_requests == []


class _FakeReconcileNode:
    """Stand-in exposing only what _reconcile_transmit_param touches."""

    def __init__(self, param_value, transmitting):
        self._params = {'transmit': types.SimpleNamespace(value=param_value)}
        self._transmitting = transmitting
        self._tx_sync = False
        self._tx_lock = threading.Lock()
        self.set_calls = []
        self.published = False
        self._control_server = types.SimpleNamespace(
            publish_state=lambda: setattr(self, 'published', True))

    def has_parameter(self, name):
        return name in self._params

    def get_parameter(self, name):
        return self._params[name]

    def set_parameters(self, params):
        for p in params:
            self.set_calls.append((p.name, p.value))
            # the guard must be set while the write happens
            assert self._tx_sync is True
            self._params[p.name] = types.SimpleNamespace(value=p.value)
        return [types.SimpleNamespace(successful=True)]


def test_reconcile_writes_param_when_out_of_sync():
    node = _FakeReconcileNode(param_value=False, transmitting=True)
    GarminSidescanNode._reconcile_transmit_param(node)
    assert node.set_calls == [('transmit', True)]
    assert node._tx_sync is False        # guard reset after the write
    assert node.published is True         # panel refreshed promptly


def test_reconcile_noop_when_in_sync():
    node = _FakeReconcileNode(param_value=True, transmitting=True)
    GarminSidescanNode._reconcile_transmit_param(node)
    assert node.set_calls == []
    assert node.published is False


# ----- device auto-detect from render-layer structure --------------------

def _img_packet(later_sh, bracket=0x13):
    # eb07 + 4-byte len + sub-header (bracket at offset 13) + FH + `later_sh`
    # SH headers. Generation comes from the SH count, NOT the bracket byte.
    head = bytes([0xeb, 0x07, 0, 0]) + bytes(4) + bytes([0x0e, 1, 3, 9, 0, bracket])
    return head + FH + bytes(20) + (SH + bytes(8)) * later_sh


def _node():
    return types.SimpleNamespace(_detected_gen=None, _gen_votes={})


def _detect(node, payload):
    return GarminSidescanNode._detect_generation(node, payload)


def test_detect_generation_votes_then_latches_gcv20():
    node = _node()
    for _ in range(GEN_VOTE_MIN - 1):              # not enough votes yet
        assert _detect(node, _img_packet(0)) is None     # FH only -> gcv20 vote
    assert _detect(node, _img_packet(1)) == 'gcv20'      # <=1 SH still gcv20; latches
    assert _detect(node, _img_packet(2)) == 'gcv20'      # latched, a gcv10 packet can't flip it


def test_detect_generation_gcv10_by_layer_count():
    node = _node()
    for _ in range(GEN_VOTE_MIN):
        _detect(node, _img_packet(2))              # 2 SH (3 layers) -> gcv10
    assert node._detected_gen == 'gcv10'


def test_detect_generation_ignores_bracket_and_skips_non_sample():
    # Same render structure but different bracket bytes both vote gcv20 -- byte 13
    # is the range bracket, not the generation.
    node = _node()
    for tag in (0x11, 0x12, 0x13, 0x11, 0x12):
        _detect(node, _img_packet(0, bracket=tag))
    assert node._detected_gen == 'gcv20'
    # non-sample packets never vote
    node = _node()
    assert _detect(node, b'\xeb\x07' + bytes(40)) is None   # eb07 but no FH layer
    assert _detect(node, b'\xd8\x07' + bytes(40)) is None   # not an eb07 packet
    assert node._detected_gen is None and node._gen_votes == {}


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
    node._sound_speed = 1500.0
    node._ping_count = {'port': 0, 'stbd': 0, 'down': 0}
    node._pub_status = types.SimpleNamespace(publish=lambda msg: None)
    GarminSidescanNode._publish_status(node)
    assert node.publishes == 1


# ----- nadir bottom range (sub-header v1 -> sensor_msgs/Range, issue #16) --

def test_build_nadir_range_maps_bottom_range_to_downward_range():
    from builtin_interfaces.msg import Time
    from sensor_msgs.msg import Range

    msg = build_nadir_range(14.58, 'gs_nadir', Time(sec=5, nanosec=0),
                            field_of_view=0.2, max_range=60.0)
    assert msg.header.frame_id == 'gs_nadir'        # dedicated +X-down frame
    assert msg.header.stamp.sec == 5                # ping receive-time stamp
    assert msg.radiation_type == Range.ULTRASOUND
    assert abs(msg.range - 14.58) < 1e-4            # v1 bottom range -> range
    assert msg.min_range == 0.0                     # shallow not flagged invalid
    assert abs(msg.max_range - 60.0) < 1e-4
    assert abs(msg.field_of_view - 0.2) < 1e-4


def test_make_sonar_msg_down_never_takes_commanded_range_fallback():
    # The commanded range is the side-scan swath; the down-look's true range
    # is its auto-ranged water-column extent. With no parseable sub-header
    # (e.g. GCV-10) the side channels may fall back to the commanded range,
    # but the down channel must publish "unavailable" rather than a
    # confidently-wrong ~2x scale.
    from builtin_interfaces.msg import Time

    def fake(side):
        return types.SimpleNamespace(
            _frame_id='gs', _freq={side: 0.0},
            _sound_speed=1500.0,
            _controls={'range': '50.0'},
            _bytes_per_sample=2, _sample_rate=0.0, _sonar_dtype=0,
            _make_sonar_msg=GarminSidescanNode._make_sonar_msg)
    samples = bytes(4096)                       # 2048 uint16 bins
    stamp = types.SimpleNamespace(to_msg=lambda: Time())
    side_msg = GarminSidescanNode._make_sonar_msg(
        fake('port'), 'port', samples, stamp, sub=None)
    down_msg = GarminSidescanNode._make_sonar_msg(
        fake('down'), 'down', samples, stamp, sub=None)
    # side: commanded fallback engages -> range round-trips to 50.0
    assert abs(1500.0 * 2048 / (2.0 * side_msg.sample_rate) - 50.0) < 1e-6
    # down: no commanded fallback -> 0.0 = unavailable
    assert down_msg.sample_rate == 0.0


def test_make_sonar_msg_encodes_near_field_gate():
    # The GCV delivers the gated TAIL of a fixed 2048-sample line spanning
    # [0, display_range]; the omitted near-field head must be exposed as sample0
    # so a consumer places delivered sample j at range sv*(sample0+j)/(2*rate),
    # NOT starting at range 0. Validated against the device's own down-look
    # bottom echo: display_range 1.843 m, 1943 delivered bins -> sample0 105,
    # and the bottom-echo bin (319) recovers the reported bottom_range_m (~0.381 m).
    from builtin_interfaces.msg import Time
    from garmin_sidescan.decode import GRID_BINS, Subheader

    sub = Subheader(channel=2, layer=0x0d, v1_tag=0x12,
                    bottom_range_m=0.381, display_range_m=1.843,
                    near_field_m=0.0985)
    node = types.SimpleNamespace(
        _frame_id='gs', _freq={'down': 0.0}, _sound_speed=1500.0,
        _controls={'range': '0.0'}, _bytes_per_sample=2, _sample_rate=0.0,
        _sonar_dtype=0, get_logger=lambda: _FakeLogger())
    samples = bytes(2 * 1943)                    # 1943 uint16 bins
    stamp = types.SimpleNamespace(to_msg=lambda: Time())
    msg = GarminSidescanNode._make_sonar_msg(node, 'down', samples, stamp, sub=sub)

    assert msg.samples_per_beam == 1943
    assert msg.sample0 == GRID_BINS - 1943       # == 105, the near-field gate
    # full display range recovers from the FULL grid (sample0 + bins)
    full = 1500.0 * (msg.sample0 + msg.samples_per_beam) / (2.0 * msg.sample_rate)
    assert abs(full - 1.843) < 1e-3
    # the bottom echo at delivered bin 319 -> grid index sample0+319 -> ~0.381 m
    range_319 = 1500.0 * (msg.sample0 + 319) / (2.0 * msg.sample_rate)
    assert abs(range_319 - 0.381) < 5e-3
    # a consumer that ignored sample0 would shift every sample inward by the
    # near-field offset (~0.094 m here) and badly misplace the bottom
    ignored = 1500.0 * 319 / (2.0 * msg.sample_rate)
    assert range_319 - ignored > 0.08


def test_emit_ping_skips_implausible_nadir_values():
    from garmin_sidescan.decode import ScanLine, Subheader

    published = []

    def sub(v1, v2):
        return Subheader(channel=2, layer=0x0d, v1_tag=0x12,
                         bottom_range_m=v1, display_range_m=v2,
                         near_field_m=0.1)

    def fake():
        return types.SimpleNamespace(
            _chan_side={2: 'down'},
            _ping_count={'down': 0},
            _pub_sonar={'down': types.SimpleNamespace(publish=lambda m: None)},
            _pub_depth=types.SimpleNamespace(publish=published.append),
            _make_sonar_msg=lambda *a, **k: None,
            _nadir_frame_id='gs_nadir', _nadir_fov=0.0,
            _emit_ping=GarminSidescanNode._emit_ping)
        # _make_sonar_msg stubbed: this test pins only the nadir gate

    stamp = types.SimpleNamespace(to_msg=lambda: None)
    node = fake()
    # plausible: 0 < v1 <= v2 -> published with max_range = v2
    GarminSidescanNode._emit_ping(node, ScanLine(2, b'xx', stamp, sub(7.5, 21.0), 16))
    assert len(published) == 1 and abs(published[0].max_range - 21.0) < 1e-6
    # corrupt: v1 beyond the observable window -> no spec-invalid Range
    GarminSidescanNode._emit_ping(node, ScanLine(2, b'xx', stamp, sub(30.0, 21.0), 16))
    # degenerate: non-positive v1 -> skipped
    GarminSidescanNode._emit_ping(node, ScanLine(2, b'xx', stamp, sub(0.0, 21.0), 16))
    assert len(published) == 1
