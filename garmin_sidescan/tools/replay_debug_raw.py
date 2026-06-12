#!/usr/bin/env python3
"""
Replay a recorded ``debug/raw`` stream onto the GCV imagery multicast.

Hardware-free end-to-end driver testing: feeds the **actual driver node** the
same UDP datagrams a live GCV produced, paced by the bag's receive timestamps,
so every layer from the multicast socket up (generation vote, assembler,
sub-header scaling, ``nadir_depth``) runs exactly as on the boat.  Point a
``ros2 topic echo`` / rqt waterfall at the driver's output while replaying.

Run the driver against the replay on the same host::

    ros2 run garmin_sidescan garmin_sidescan --ros-args \
        -p iface_ip:=127.0.0.1 -p filter_src:=false -p require_sound_speed:=false

    python3 replay_debug_raw.py BAG [--rate 1.0] [--start S] [--end S]

``filter_src:=false`` is required (replayed packets carry the replayer's
source address, not the GCV's). ``iface_ip:=127.0.0.1`` on both ends keeps
the multicast on loopback — nothing leaks onto a live boat network, and the
replay works with no NIC configured. The driver stays passive: it never
transmits unless commanded, and there is no GCV TCP endpoint here anyway.

Requires a bag recorded with ``debug_raw:=true``. Run in a sourced workspace.
"""
import argparse
import socket
import sys
import time

from rclpy.serialization import deserialize_message
import rosbag2_py
from std_msgs.msg import UInt8MultiArray


def _reader(bag):
    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=bag, storage_id='mcap'),
           rosbag2_py.ConverterOptions(input_serialization_format='cdr',
                                       output_serialization_format='cdr'))
    return r


def find_raw_topic(bag):
    """Return the first topic ending in ``debug/raw``, or None."""
    for t in _reader(bag).get_all_topics_and_types():
        if t.name.endswith('debug/raw'):
            return t.name
    return None


def open_sender(iface_ip, ttl):
    """Return a UDP socket configured to send multicast on ``iface_ip``."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    # Loop back to local receivers (the driver under test on this host).
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    if iface_ip:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                        socket.inet_aton(iface_ip))
    return sock


def replay(bag, raw_topic, dest, iface_ip, ttl, rate, start, end):
    """Send the bag's raw payloads to ``dest``, paced by bag timestamps."""
    sock = open_sender(iface_ip, ttl)
    r = _reader(bag)
    t0 = None            # first bag timestamp (ns)
    wall0 = None         # wall clock at the first sent packet
    sent = 0
    try:
        while r.has_next():
            topic, data, ts = r.read_next()
            if topic != raw_topic:
                continue
            if t0 is None:
                t0 = ts
            rel = (ts - t0) / 1e9
            if rel < start:
                continue
            if rel > end:
                break
            if wall0 is None:
                wall0 = time.monotonic() - rel / rate
            lag = rel / rate - (time.monotonic() - wall0)
            if lag > 0:
                time.sleep(lag)
            payload = bytes(deserialize_message(data, UInt8MultiArray).data)
            sock.sendto(payload, dest)
            sent += 1
            if sent % 5000 == 0:
                print(f'  t={rel:7.1f}s  {sent} datagrams', flush=True)
    except KeyboardInterrupt:
        print('\ninterrupted', file=sys.stderr)
    finally:
        sock.close()
    print(f'replayed {sent} datagrams to {dest[0]}:{dest[1]}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('bag', help='path to an mcap bag recorded with debug_raw:=true')
    ap.add_argument('--group', default='239.254.2.1',
                    help='destination multicast group (driver mcast_group)')
    ap.add_argument('--port', type=int, default=50220,
                    help='destination port (driver mcast_port)')
    ap.add_argument('--iface-ip', default='127.0.0.1',
                    help='interface to send multicast on (default loopback; '
                         'match the driver iface_ip)')
    ap.add_argument('--ttl', type=int, default=0,
                    help='multicast TTL (default 0 = never leaves the host)')
    ap.add_argument('--rate', type=float, default=1.0,
                    help='playback speed multiplier')
    ap.add_argument('--start', type=float, default=0.0,
                    help='window start (s, relative to the first raw datagram)')
    ap.add_argument('--end', type=float, default=1e9, help='window end (s)')
    args = ap.parse_args(argv)

    if args.rate <= 0:
        sys.exit('error: --rate must be > 0 (it divides the pacing deadline)')
    raw_topic = find_raw_topic(args.bag)
    if raw_topic is None:
        sys.exit('error: no */debug/raw topic in the bag — record with debug_raw:=true')
    print(f'replaying {raw_topic}\n  -> {args.group}:{args.port} '
          f'(iface {args.iface_ip or "default"}, ttl {args.ttl}, x{args.rate})')
    replay(args.bag, raw_topic, (args.group, args.port), args.iface_ip,
           args.ttl, args.rate, args.start, args.end)


if __name__ == '__main__':
    main()
