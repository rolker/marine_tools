# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Bridge Kongsberg ``.all`` Raw Range and Angle 78 datagrams to ROS.

Listens for the M3's UDP-exported ``.all`` stream, decodes the N/78 datagram
(the one the M3 populates -- its XYZ88 is exported empty), and publishes one
``marine_acoustic_msgs/SonarDetections`` per ping on ``detections``. The
downstream ``cube_bathymetry/detections_to_pointcloud`` node turns that into a
``PointCloud2`` with CUBE-model uncertainty -- so this node does no geometry or
TPU itself; it is purely a wire-format translator. See marine_tools#1.

A latched ``marine_interfaces/SonarInfo`` companion is published on
``sonar_info`` (ADR-0009): the acquisition settings (pulse length, bandwidth,
signal type per TX sector) needed for GeoCoder-style radiometric backscatter
correction, plus what the ``intensities`` values mean. Re-published on change
and on a slow heartbeat so every rosbag2 split segment captures one. See
marine_tools#69.
"""

import datetime
import math
import os
import socket
import struct
import threading
import time

from builtin_interfaces.msg import Time as TimeMsg
from kongsberg_em_bridge import angular_response
from kongsberg_em_bridge import em_datagrams as em
from marine_acoustic_msgs.msg import DetectionFlag, PingInfo, SonarDetections
from marine_interfaces.msg import SonarInfo
import rclpy
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, qos_profile_sensor_data, QoSProfile,
                       ReliabilityPolicy)
from std_srvs.srv import SetBool


def recording_transition(currently_recording, want_on, save_dir):
    """
    Pure decision for the ``set_recording`` service.

    Returns ``(action, ok, message)`` where ``action`` is ``'open'`` /
    ``'close'`` / ``'noop'`` (what the caller should do to ``_save_file``).
    Both directions are idempotent, and turning on with no ``save_all_dir``
    configured is rejected (``ok=False``) rather than silently doing nothing.
    """
    if want_on:
        if currently_recording:
            return 'noop', True, 'already recording'
        if not save_dir:
            return 'noop', False, 'no save_all_dir configured; cannot record'
        return 'open', True, 'recording started'
    if not currently_recording:
        return 'noop', True, 'not recording'
    return 'close', True, 'recording stopped'


def save_rollover_due(elapsed_s, bytes_written, max_seconds, max_bytes):
    """
    Return ``(should_roll, reason)`` for a ``.all`` recording segment.

    A trigger arms only when its limit is > 0, so the default (0/0) never rolls.
    Time and size arm independently; time is reported first when both fire.
    ``elapsed_s`` may be ``None`` before the segment's open time is known, in
    which case the time trigger is inert. Pure (no I/O) so it is unit-tested
    without an rclpy node.
    """
    if max_seconds > 0 and elapsed_s is not None and elapsed_s >= max_seconds:
        return True, 'time'
    if max_bytes > 0 and bytes_written >= max_bytes:
        return True, 'size'
    return False, ''


# Kongsberg N/78 signal waveform identifier -> SonarInfo signal type.
_WAVEFORM_TO_SIGNAL_TYPE = {
    0: SonarInfo.SIGNAL_TYPE_CW,
    1: SonarInfo.SIGNAL_TYPE_FM_UP,
    2: SonarInfo.SIGNAL_TYPE_FM_DOWN,
}


def sonar_model_name(model):
    """
    Map the .all model number to a SonarInfo.sonar_model string.

    30 = M3 (per the live captures the N/78 decode was validated against);
    anything else passes through untranslated as ``kongsberg-em<model>``.
    """
    return 'kongsberg-m3' if model == 30 else f'kongsberg-em{model}'


def acquisition_signature(parsed):
    """
    Return the SonarInfo-relevant slice of an N/78 ping as a hashable tuple.

    Drives republish-on-change: two pings with equal signatures need no new
    SonarInfo. Pure so it is unit-tested without an rclpy node.
    """
    return (parsed['model'],
            tuple((s['signal_length'], s['waveform'], s['bandwidth'])
                  for s in parsed['sectors']))


def sonar_info_from_parsed(parsed, frame_id, stamp, angular=None):
    """
    Build the latched ``SonarInfo`` companion (ADR-0009) for an N/78 ping.

    Acquisition settings come from the datagram's TX-sector blocks; the
    intensity-semantics axes describe what this bridge's
    ``SonarDetections.intensities`` actually are (N/78 per-beam reflectivity,
    published as already-scaled dB floats: a relative, uncalibrated power
    ratio). Correction state is honest-unknown per the SonarInfo conventions
    block -- the bridge applies nothing, but what gain/normalization the
    sonar applied before exporting reflectivity is unverified -- and the NaN
    sentinels are set explicitly (rosidl float defaults would silently claim
    0 dB values).

    ``angular`` is an optional ``(points, tl_removed, absorption_db_per_m)``
    triple from ``angular_response.load_angular_response_curve``
    (marine_tools#71): the sensor's empirical angular-response calibration,
    declared with its TL provenance (uma#268) so consumers know whether the
    curve is a tier-2 TL-removed residual. None / empty points leaves the
    curve fields empty with honestly-UNKNOWN provenance.

    Pure so it is unit-tested without an rclpy node.
    """
    msg = SonarInfo()
    msg.header.stamp = stamp
    msg.header.frame_id = frame_id
    msg.sonar_model = sonar_model_name(parsed['model'])
    for sector in parsed['sectors']:
        msg.pulse_lengths.append(float(sector['signal_length']))
        msg.bandwidths.append(float(sector['bandwidth']))
        msg.tx_signal_types.append(_WAVEFORM_TO_SIGNAL_TYPE.get(
            sector['waveform'], SonarInfo.SIGNAL_TYPE_UNKNOWN))
    msg.intensity_quantity = SonarInfo.QUANTITY_POWER
    msg.intensity_scale = SonarInfo.INTENSITY_SCALE_DB
    msg.intensity_reference = SonarInfo.REFERENCE_UNCALIBRATED_RELATIVE
    msg.scale = 1.0    # intensities are published as physical dB already
    msg.offset = 0.0
    msg.tvg_model = SonarInfo.TVG_UNKNOWN
    msg.tvg_absorption_db_per_km = math.nan
    msg.source_level_db = math.nan
    msg.angular_normalization = SonarInfo.ANGULAR_NORMALIZATION_UNKNOWN
    points, tl_removed, absorption = angular or ([], False, None)
    for angle_deg, db_rel in points:
        msg.angular_response_angle_deg.append(float(angle_deg))
        msg.angular_response_db_rel_nadir.append(float(db_rel))
    if not points:
        msg.angular_response_tl = SonarInfo.ANGULAR_RESPONSE_TL_UNKNOWN
        msg.angular_response_absorption_db_per_m = math.nan
    elif tl_removed:
        msg.angular_response_tl = SonarInfo.ANGULAR_RESPONSE_TL_REMOVED
        # Verbatim from the CSV header: consumers apply alpha as-is in
        # 40*log10(R) + 2*alpha*R and never recompute it (cube#87). A tier-2
        # header missing its absorption yields None from the loader -> NaN
        # here, so the consumer sees "alpha unknown" instead of a fabricated
        # 0.0 that would silently drop the absorption term.
        msg.angular_response_absorption_db_per_m = (
            math.nan if absorption is None else float(absorption))
    else:
        msg.angular_response_tl = SonarInfo.ANGULAR_RESPONSE_TL_IN
        msg.angular_response_absorption_db_per_m = math.nan
    return msg


class KongsbergEmBridge(Node):
    """Decode M3 ``.all`` N/78 datagrams from UDP into ``SonarDetections``."""

    def __init__(self):
        super().__init__('kongsberg_em_bridge')

        self.declare_parameter('bind_address', '0.0.0.0')
        self.declare_parameter('bind_port', 20002)
        self.declare_parameter('frame_id', 'm3')
        # Drop beams the sonar flagged invalid. Required: the CUBE error model
        # iterates every element of two_way_travel_times and does NOT consult
        # flags, so invalid (twtt=0) beams would otherwise become z=0 points.
        self.declare_parameter('skip_invalid_beams', True)
        # Directory in which to record the raw datagram stream as a genuine
        # Kongsberg ``.all`` file (loadable by Caris/Qimera/MB-System). Empty
        # disables recording. Each node run writes a fresh timestamped file so
        # restarts never overwrite or interleave prior recordings.
        self.declare_parameter('save_all_dir', '')
        # PROTOTYPE rollover: split the .all recording into a fresh file once
        # the current one reaches this many wall-clock seconds and/or bytes.
        # 0 (default) disables that trigger; the two arm independently, so e.g.
        # max_seconds=600 + max_bytes=0 gives 10-minute files regardless of
        # size. NOTE (deferred): split segments do NOT yet re-emit the
        # installation/runtime/SVP datagrams (e.g. I/73) at their head, so a
        # mid-stream segment may not load/georeference cleanly in some readers.
        self.declare_parameter('save_all_max_seconds', 0.0)
        self.declare_parameter('save_all_max_bytes', 0)
        # .all recording is a debugging aid that can consume a lot of disk, so
        # it is OPT-IN on every platform: default off. save_all_dir still
        # configures *where* it writes; set record_on_start=true to record from
        # startup, or arm it at runtime via the set_recording service. #54.
        self.declare_parameter('record_on_start', False)

        # SonarInfo heartbeat period in seconds (ADR-0009 recommends <= 10 s:
        # rosbag2 does not re-persist a latched message into new split
        # segments, so change-only publishing would leave later segments with
        # no SonarInfo). The per-segment guarantee only holds while this
        # period is shorter than the recorder's shortest split segment --
        # size-based splits can produce segments shorter than a lazy period.
        # <= 0 disables the heartbeat (change-only; not recommended when
        # recording).
        self.declare_parameter('sonar_info_period', 10.0)
        # Empirical angular-response calibration curve CSV (written by
        # cube_bathymetry's derive_angular_response.py) to declare in
        # SonarInfo with its TL provenance -- the CameraInfo model: the
        # driver publishes the sensor's calibration so it rides in the bags
        # beside the data it corrects (marine_tools#71, consumers
        # cube_bathymetry#102). Empty = no curve (fields stay empty with
        # honest-unknown provenance). Loaded once at startup: calibration,
        # not an operator setting.
        self.declare_parameter('angular_response_curve_file', '')

        self.frame_id = self.get_parameter('frame_id').value
        self.skip_invalid = bool(self.get_parameter('skip_invalid_beams').value)

        self.publisher = self.create_publisher(
            SonarDetections, 'detections', qos_profile_sensor_data)

        # Latched per-sensor metadata companion (ADR-0009 / marine_tools#69).
        self.sonar_info_pub = self.create_publisher(
            SonarInfo, 'sonar_info',
            QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL))
        # Guards the last-published state across the recv thread (change
        # detection in _publish) and the executor thread (heartbeat timer);
        # both publish under the lock so a heartbeat stamp update can never
        # tear a concurrent serialization.
        self._sonar_info_lock = threading.Lock()
        self._last_sonar_info = None
        self._last_acq_sig = None
        curve_file = str(
            self.get_parameter('angular_response_curve_file').value).strip()
        self._angular_response = angular_response.load_angular_response_curve(
            curve_file)
        if curve_file and not self._angular_response[0]:
            self.get_logger().warning(
                f'angular_response_curve_file={curve_file!r} yielded an '
                f'EMPTY curve -- SonarInfo will declare no angular response')
        elif self._angular_response[0]:
            points, tl_removed, alpha = self._angular_response
            self.get_logger().info(
                f'angular-response curve: {len(points)} points from '
                f'{curve_file!r} (tl_removed={tl_removed}, '
                f'absorption={alpha} dB/m)')
        period = float(self.get_parameter('sonar_info_period').value)
        self._sonar_info_timer = (
            self.create_timer(period, self._sonar_info_heartbeat)
            if period > 0 else None)

        self._save_dir = str(self.get_parameter('save_all_dir').value).strip()
        self._save_max_seconds = float(
            self.get_parameter('save_all_max_seconds').value)
        self._save_max_bytes = int(self.get_parameter('save_all_max_bytes').value)
        # Per-segment rollover bookkeeping (reset by _open_save_file).
        self._save_started = None     # time.monotonic() when current file opened
        self._save_bytes = 0          # bytes written to the current segment
        record_on_start = bool(self.get_parameter('record_on_start').value)
        self._save_file = (
            self._open_save_file(self._save_dir) if record_on_start else None)
        self._save_count = 0
        # Guards _save_file across the recv thread (_record) and the main
        # thread (destroy_node). The thread join in destroy_node uses a
        # timeout, so the recv thread can still be mid-write when shutdown
        # closes the file -- the lock makes the None-check/write/close atomic.
        self._save_lock = threading.Lock()
        # Runtime on/off for .all recording (no node restart needed): true =
        # start a fresh segment, false = close the current file. See #54.
        self._set_recording_srv = self.create_service(
            SetBool, '~/set_recording', self._set_recording_cb)

        addr = self.get_parameter('bind_address').value
        port = int(self.get_parameter('bind_port').value)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(0.5)
        self.sock.bind((addr, port))

        self._ping_count = 0
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()
        self.get_logger().info(
            f'kongsberg_em_bridge listening on {addr}:{port} (UDP), '
            f'publishing SonarDetections on "detections" + latched SonarInfo '
            f'on "sonar_info", frame "{self.frame_id}"')

    def _open_save_file(self, save_dir):
        """
        Open a fresh timestamped ``.all`` file in ``save_dir``, or return None.

        Returns the open binary file handle, or ``None`` if recording is
        disabled (empty dir) or the file cannot be created -- a recording
        failure must never stop the node from bridging live data.
        """
        if not save_dir:
            return None
        try:
            os.makedirs(save_dir, exist_ok=True)
            path = self._unique_all_path(save_dir)
            handle = open(path, 'wb')
        except OSError as exc:
            self.get_logger().error(
                f'could not open .all recording in {save_dir}: {exc}; '
                f'continuing without recording')
            return None
        self._save_started = time.monotonic()
        self._save_bytes = 0
        self.get_logger().info(f'recording received datagrams to {path}')
        return handle

    @staticmethod
    def _unique_all_path(save_dir):
        """
        Return a fresh ``m3_<UTC>.all`` path in ``save_dir`` that does not exist.

        The base name is second-resolution, so a size-triggered roll inside the
        same second would collide; a ``_NN`` suffix is appended in that case so
        the ``'wb'`` open never truncates a just-written segment.
        """
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            '%Y%m%d_%H%M%S')
        path = os.path.join(save_dir, f'm3_{stamp}.all')
        n = 1
        while os.path.exists(path):
            path = os.path.join(save_dir, f'm3_{stamp}_{n:02d}.all')
            n += 1
        return path

    def _set_recording_cb(self, request, response):
        """Start/stop ``.all`` recording at runtime (``std_srvs/SetBool``)."""
        with self._save_lock:
            action, ok, message = recording_transition(
                self._save_file is not None, request.data, self._save_dir)
            if action == 'open':
                self._save_file = self._open_save_file(self._save_dir)
                if self._save_file is None:
                    ok = False
                    message = f'failed to open .all file in {self._save_dir!r}'
                else:
                    self._save_count = 0
            elif action == 'close':
                try:
                    self._save_file.close()
                except OSError:
                    pass
                message = f'recording stopped ({self._save_count} datagrams)'
                self._save_file = None
        self.get_logger().info(f'set_recording({request.data}): {message}')
        response.success = ok
        response.message = message
        return response

    def _record(self, data):
        """Append one received datagram to the ``.all`` file, if recording."""
        with self._save_lock:
            if self._save_file is None:
                return
            try:
                framed = em.frame_all_record(data)
                self._save_file.write(framed)
                self._save_file.flush()
                self._save_count += 1
                self._save_bytes += len(framed)
            except (OSError, ValueError) as exc:
                # Disable recording on write failure (e.g. disk full, or a
                # write that lost the close/shutdown race -> "write to closed
                # file" ValueError) rather than warn on every packet -- the
                # live bridge must keep running.
                self.get_logger().error(
                    f'.all recording write failed: {exc}; recording disabled')
                try:
                    self._save_file.close()
                except OSError:
                    pass
                self._save_file = None
                return
            # Roll AFTER a successful write so a completed segment always ends
            # on a whole datagram boundary (valid .all framing).
            self._maybe_roll()

    def _maybe_roll(self):
        """
        Roll to a fresh ``.all`` segment if a time or size limit is reached.

        Called under ``_save_lock`` with ``_save_file`` open. A reopen failure
        leaves ``_save_file`` None (recording stops, logged in
        ``_open_save_file``) while the live bridge keeps running. PROTOTYPE: the
        new segment does not re-emit installation/runtime datagrams, so a
        mid-stream file may lack the I/73 record some readers expect.
        """
        elapsed = (None if self._save_started is None
                   else time.monotonic() - self._save_started)
        should_roll, reason = save_rollover_due(
            elapsed, self._save_bytes, self._save_max_seconds, self._save_max_bytes)
        if not should_roll:
            return
        self.get_logger().info(
            f'rolling .all recording ({reason}): completed segment has '
            f'{self._save_bytes} bytes')
        try:
            self._save_file.close()
        except OSError:
            pass
        # _open_save_file resets _save_started/_save_bytes for the new segment.
        self._save_file = self._open_save_file(self._save_dir)

    def destroy_node(self):
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        try:
            self.sock.close()
        except OSError:
            pass
        with self._save_lock:
            if self._save_file is not None:
                try:
                    self._save_file.close()
                except OSError:
                    pass
                self.get_logger().info(
                    f'closed .all recording ({self._save_count} datagrams)')
                self._save_file = None
        super().destroy_node()

    def _recv_loop(self):
        while self._running and rclpy.ok():
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            # Record every received datagram (position, attitude, sound speed,
            # clock, N/78, ...) before the N/78 publish filter, so the saved
            # .all has the nav/attitude needed to georeference downstream.
            self._record(data)
            if len(data) <= 1 or data[0] != em.STX or \
                    data[1] != em.DG_RAW_RANGE_ANGLE_78:
                continue
            try:
                parsed = em.parse_n78(data)
            except (ValueError, struct.error) as exc:
                self.get_logger().warning(
                    f'datagram decode error: {exc}', throttle_duration_sec=10.0)
                continue
            # Publish is guarded separately and broadly: a publish-time error
            # (e.g. rclpy context mid-shutdown) must never kill this daemon
            # recv thread and leave the node silently deaf.
            try:
                self._publish(parsed)
            except Exception as exc:  # noqa: B902 - intentional: keep thread alive
                self.get_logger().warning(
                    f'publish failed: {exc}', throttle_duration_sec=10.0)

    def _stamp(self, parsed) -> TimeMsg:
        # Use the sonar's (1PPS-disciplined) ping time. Band-aid: if the
        # datagram time is missing or implausible, fall back to arrival time so
        # downstream still has a usable, monotonic-ish stamp -- no knob to get
        # this wrong.
        unix = parsed.get('unix_time')
        if unix is None:
            self.get_logger().warning(
                'datagram time unavailable; stamping with receive time',
                throttle_duration_sec=10.0)
            return self.get_clock().now().to_msg()
        msg = TimeMsg()
        sec = int(unix)
        nsec = int(round((unix - sec) * 1e9))
        if nsec >= 1_000_000_000:  # rounding can carry; keep nanosec < 1e9
            sec += 1
            nsec -= 1_000_000_000
        msg.sec = sec
        msg.nanosec = nsec
        return msg

    def _sonar_info_heartbeat(self):
        """
        Re-publish the last SonarInfo so bag split segments capture it.

        The re-publish keeps the stamp of the change it describes: pings and
        change-publishes are stamped from the sonar's 1PPS-disciplined clock
        (see _stamp), so stamping heartbeats from the system clock instead
        could place them AFTER a segment's pings whenever the clocks diverge,
        silently breaking the "most recent SonarInfo at or before the ping
        stamp" association rule. rosbag2 assigns messages to segments by
        receive time, so the unchanged header stamp does not hinder the
        split-segment purpose. Guarded like the recv-thread publish: a
        publish-time error (e.g. rclpy context mid-shutdown) must not
        propagate out of the timer callback.
        """
        try:
            with self._sonar_info_lock:
                if self._last_sonar_info is None:
                    return    # no ping decoded yet -- nothing to declare
                self.sonar_info_pub.publish(self._last_sonar_info)
        except Exception as exc:  # noqa: B902 - intentional: shutdown race
            self.get_logger().warning(
                f'sonar_info heartbeat publish failed: {exc}',
                throttle_duration_sec=10.0)

    def _maybe_publish_sonar_info(self, parsed, stamp):
        """Publish a fresh SonarInfo when the acquisition settings change."""
        sig = acquisition_signature(parsed)
        if sig == self._last_acq_sig:
            return
        info = sonar_info_from_parsed(
            parsed, self.frame_id, stamp, self._angular_response)
        with self._sonar_info_lock:
            self._last_acq_sig = sig
            self._last_sonar_info = info
            self.sonar_info_pub.publish(info)
        # Throttled like the decode-health line below: alternating multi-mode
        # pinging legitimately changes the signature every ping (each change
        # re-publishes, correctly), and an unthrottled line would flood the
        # console at survey ping rates.
        self.get_logger().info(
            f'sonar_info updated: model={info.sonar_model}, '
            f'pulse_lengths={list(info.pulse_lengths)} s, '
            f'bandwidths={list(info.bandwidths)} Hz, '
            f'signal_types={list(info.tx_signal_types)}',
            throttle_duration_sec=30.0)

    def _publish(self, parsed):
        msg = SonarDetections()
        msg.header.stamp = self._stamp(parsed)
        msg.header.frame_id = self.frame_id
        self._maybe_publish_sonar_info(parsed, msg.header.stamp)

        info = PingInfo()
        info.frequency = float(parsed['sectors'][0]['centre_frequency']
                               if parsed['sectors'] else 0.0)
        info.sound_speed = float(parsed['sound_speed'])
        # tx/rx_beamwidths left empty on purpose: the CUBE error model treats
        # those array values as DEGREES (PingInfo.msg says radians) and falls
        # back to its Device beamwidth when they are absent -- so leaving them
        # empty avoids a unit mismatch. Tracked in cube_bathymetry#30.
        msg.ping_info = info

        sectors = parsed['sectors']
        for beam in parsed['beams']:
            if self.skip_invalid and not beam['valid']:
                continue
            sector = sectors[beam['tx_sector']] if beam['tx_sector'] < len(sectors) \
                else (sectors[0] if sectors else {'tilt_deg': 0.0, 'tx_delay': 0.0})
            flag = DetectionFlag()
            flag.flag = (DetectionFlag.DETECT_OK if beam['valid']
                         else DetectionFlag.DETECT_BAD_SONAR)
            msg.flags.append(flag)
            msg.two_way_travel_times.append(float(beam['twtt']))
            msg.tx_delays.append(float(sector['tx_delay']))
            msg.intensities.append(float(beam['reflectivity_db']))
            # Deterministic convention mapping (no tuning knobs):
            #   Kongsberg .all (EM Datagram Formats 850-160692, Note 1):
            #     beam pointing angle +ve to PORT, transmit tilt +ve FORWARD.
            #   marine_acoustic_msgs/SonarDetections:
            #     rx_angles +ve to STARBOARD, tx_angles +ve FORWARD.
            # So negate the rx (pointing) angle; tx (tilt) carries through.
            # Any physical mount orientation belongs in the URDF base_link->frame
            # transform (a normally-mounted downward M3 is roll=pi), not here.
            msg.tx_angles.append(math.radians(sector['tilt_deg']))
            msg.rx_angles.append(-math.radians(beam['pointing_angle_deg']))

        self.publisher.publish(msg)
        self._ping_count += 1
        # Decode-health heartbeat, time-throttled rather than every N pings:
        # at survey ping rates a per-100-ping line prints every few seconds,
        # which floods the console. ~30 s keeps a liveness signal without spam.
        self.get_logger().info(
            f'ping {parsed["ping"]}: {len(msg.two_way_travel_times)} detections '
            f'(of {parsed["nrx"]} beams), c={info.sound_speed:.1f} m/s, '
            f'f={info.frequency / 1000.0:.0f} kHz',
            throttle_duration_sec=30.0)


def main(args=None):
    rclpy.init(args=args)
    node = KongsbergEmBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
