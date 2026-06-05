# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Replay a length-framed Kongsberg ``.all`` capture over UDP.

Feeds a sample captured by the dev tool (4-byte big-endian length + payload per
datagram) back out as UDP so ``kongsberg_em_bridge`` can be exercised offline,
with no sonar attached. Sends only the N/78 datagrams by default.

This is an argparse CLI (not an rclpy node), so pass plain flags, not ROS args.

Usage:
    ros2 run kongsberg_em_bridge replay --file /path/m3_sample.bin
    # or standalone:
    python3 -m kongsberg_em_bridge.replay --file m3_sample.bin --port 20002
"""

import argparse
import socket
import sys
import time

from kongsberg_em_bridge import em_datagrams as em


def replay(path, host, port, rate_hz, only_n78, loop):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    with open(path, 'rb') as f:
        data = f.read()
    period = 0.0 if rate_hz <= 0 else 1.0 / rate_hz
    sent = 0
    while True:
        for payload in em.iter_datagrams(data):
            if only_n78 and not (len(payload) > 1 and payload[0] == em.STX
                                 and payload[1] == em.DG_RAW_RANGE_ANGLE_78):
                continue
            sock.sendto(payload, (host, port))
            sent += 1
            if period:
                time.sleep(period)
        print(f'replayed {sent} datagram(s) to {host}:{port}')
        if not loop:
            break
        sent = 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--file', required=True)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=20002)
    ap.add_argument('--rate', type=float, default=30.0,
                    help='datagrams/sec (0 = as fast as possible)')
    ap.add_argument('--all-types', action='store_true',
                    help='replay every datagram, not just N/78')
    ap.add_argument('--loop', action='store_true')
    args = ap.parse_args(argv)
    replay(args.file, args.host, args.port, args.rate,
           only_n78=not args.all_types, loop=args.loop)


if __name__ == '__main__':
    sys.exit(main())
