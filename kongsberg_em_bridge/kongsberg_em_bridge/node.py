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
"""

import datetime
import math
import os
import socket
import struct
import threading
import time

from builtin_interfaces.msg import Time as TimeMsg
from kongsberg_em_bridge import em_datagrams as em
from marine_acoustic_msgs.msg import DetectionFlag, PingInfo, SonarDetections
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
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

        self.frame_id = self.get_parameter('frame_id').value
        self.skip_invalid = bool(self.get_parameter('skip_invalid_beams').value)

        self.publisher = self.create_publisher(
            SonarDetections, 'detections', qos_profile_sensor_data)

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
            f'publishing SonarDetections on "detections", frame "{self.frame_id}"')

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

    def _publish(self, parsed):
        msg = SonarDetections()
        msg.header.stamp = self._stamp(parsed)
        msg.header.frame_id = self.frame_id

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
