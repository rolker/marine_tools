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

import math
import socket
import struct
import threading

from builtin_interfaces.msg import Time as TimeMsg
from kongsberg_em_bridge import em_datagrams as em
from marine_acoustic_msgs.msg import DetectionFlag, PingInfo, SonarDetections
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data


class KongsbergEmBridge(Node):
    """Decode M3 ``.all`` N/78 datagrams from UDP into ``SonarDetections``."""

    def __init__(self):
        super().__init__('kongsberg_em_bridge')

        self.declare_parameter('bind_address', '0.0.0.0')
        self.declare_parameter('bind_port', 20002)
        self.declare_parameter('frame_id', 'm3')
        # TEMPORARY calibration knobs: sign of the rx (across-track) and tx
        # (along-track) angles relative to the SonarDetections convention
        # (+rx to starboard, +tx forward). To be removed and hard-coded once a
        # patch test confirms the M3's handedness (see marine_tools#14).
        self.declare_parameter('rx_angle_sign', 1.0)
        self.declare_parameter('tx_angle_sign', 1.0)
        # Drop beams the sonar flagged invalid. Required: the CUBE error model
        # iterates every element of two_way_travel_times and does NOT consult
        # flags, so invalid (twtt=0) beams would otherwise become z=0 points.
        self.declare_parameter('skip_invalid_beams', True)

        self.frame_id = self.get_parameter('frame_id').value
        self.rx_sign = float(self.get_parameter('rx_angle_sign').value)
        self.tx_sign = float(self.get_parameter('tx_angle_sign').value)
        self.skip_invalid = bool(self.get_parameter('skip_invalid_beams').value)

        self.publisher = self.create_publisher(
            SonarDetections, 'detections', qos_profile_sensor_data)

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

    def destroy_node(self):
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        try:
            self.sock.close()
        except OSError:
            pass
        super().destroy_node()

    def _recv_loop(self):
        while self._running and rclpy.ok():
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                if len(data) > 1 and data[0] == em.STX and \
                        data[1] == em.DG_RAW_RANGE_ANGLE_78:
                    self._publish(em.parse_n78(data))
            except (ValueError, struct.error) as exc:
                self.get_logger().warning(
                    f'datagram decode error: {exc}', throttle_duration_sec=10.0)

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
        msg.sec = int(unix)
        msg.nanosec = int(round((unix - msg.sec) * 1e9))
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
            msg.tx_angles.append(self.tx_sign * math.radians(sector['tilt_deg']))
            msg.rx_angles.append(self.rx_sign * math.radians(beam['pointing_angle_deg']))

        self.publisher.publish(msg)
        self._ping_count += 1
        if self._ping_count % 100 == 1:
            self.get_logger().info(
                f'ping {parsed["ping"]}: {len(msg.two_way_travel_times)} detections '
                f'(of {parsed["nrx"]} beams), c={info.sound_speed:.1f} m/s, '
                f'f={info.frequency / 1000.0:.0f} kHz')


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
