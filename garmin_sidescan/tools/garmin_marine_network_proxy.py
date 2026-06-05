#!/usr/bin/env python3
"""
Garmin Marine Network proxy / relay.

Bridges a Garmin GCV sidescan sonar (on the Garmin "Marine Network",
172.16.0.0/16) to a ROS host (e.g. gabby) whose NIC will not link the Garmin
PHY directly. Run this on a host that DOES link the Garmin (here: mercat's
"Ethernet 2", 172.16.55.235/16) and also has a NIC on the ROS host's LAN
(here: "BRIDGE", 192.168.20.8/24, gabby = 192.168.20.5).

It relays the two channels the ``garmin_sidescan`` driver uses, exactly as the
driver expects them, so the *unmodified* driver runs on gabby:

* **Imagery** - GCV UDP multicast ``239.254.2.1:50220`` (received on the Marine
  Network NIC) is forwarded as **unicast** UDP to the ROS host on the same port.
  The relay's send socket is bound to ``--listen-ip`` so each datagram's source
  address is ``--listen-ip``; set the driver's ``gcv_ip`` to ``--listen-ip`` and
  its ``filter_src`` check passes. (``--re-multicast`` additionally re-emits the
  group on the ROS-host LAN, for hosts that genuinely join it there.)

* **Control** - TCP ``<listen-ip>:50227`` from the driver is forwarded to the
  GCV at ``<gcv-ip>:50227``. The driver opens one short connection per command
  and serializes them; connections are handled on a thread each regardless.

There is no host->GCV imagery path (UDP relay is one-way) and nothing reaches
the GCV except the driver's own control frames, so the sonar's transmit safety
(the sound-speed watchdog) stays entirely in the driver on gabby.

On gabby, run the stock driver pointed at the proxy::

    ros2 launch garmin_sidescan garmin_sidescan.launch.py \
        gcv_ip:=192.168.20.8 iface_ip:=192.168.20.5

Stdlib only - no third-party packages. Companion to the garmin_sidescan ROS 2
driver (see ../README.md).
"""
import argparse
import socket
import struct
import threading
import time


def log(msg):
    """Timestamped line to stdout."""
    print(f'{time.strftime("%H:%M:%S")}  {msg}', flush=True)


# --------------------------------------------------------------------------- #
# Control: TCP <listen_ip>:port  ->  GCV <gcv_ip>:port
# --------------------------------------------------------------------------- #
def _pump(src, dst):
    """Copy src -> dst until EOF, then half-close dst's send side."""
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _handle_control_client(client, peer, gcv_ip, control_port):
    """Relay one driver TCP connection through to the GCV control port."""
    upstream = None
    try:
        upstream = socket.create_connection((gcv_ip, control_port), timeout=4.0)
        upstream.settimeout(None)
        client.settimeout(None)
        # GCV is effectively fire-and-forget, but pump both ways so any reply
        # (and the client's close) is handled cleanly.
        t = threading.Thread(target=_pump, args=(upstream, client), daemon=True)
        t.start()
        _pump(client, upstream)
        t.join(timeout=4.0)
        log(f'control: {peer} -> {gcv_ip}:{control_port}  (relayed)')
    except OSError as exc:
        log(f'control: relay error for {peer}: {exc}')
    finally:
        for s in (client, upstream):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def _control_loop(listen_ip, control_port, gcv_ip, stop):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((listen_ip, control_port))
    listener.listen(8)
    listener.settimeout(1.0)
    log(f'control: listening on {listen_ip}:{control_port}')
    try:
        while not stop.is_set():
            try:
                client, addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(
                target=_handle_control_client,
                args=(client, f'{addr[0]}:{addr[1]}', gcv_ip, control_port),
                daemon=True).start()
    finally:
        listener.close()


# --------------------------------------------------------------------------- #
# Imagery: multicast 239.254.2.1:port (Marine Network)  ->  unicast to ROS host
# --------------------------------------------------------------------------- #
def _open_mcast_rx(iface_ip, group, port):
    """Join the GCV imagery group on a specific local interface."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', port))
    mreq = socket.inet_aton(group) + socket.inet_aton(iface_ip)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(1.0)
    return sock


def _imagery_loop(args, stop):
    rx = _open_mcast_rx(args.garmin_iface_ip, args.mcast_group, args.imagery_port)

    # Bind tx to listen_ip so forwarded datagrams' SOURCE is listen_ip; the
    # driver's filter_src compares the source against gcv_ip (= listen_ip).
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.bind((args.listen_ip, 0))

    targets = [(h, args.imagery_port) for h in args.relay_to]
    if args.re_multicast:
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                      socket.inet_aton(args.listen_ip))
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        targets.append((args.mcast_group, args.imagery_port))

    log(f'imagery: joined {args.mcast_group}:{args.imagery_port} on '
        f'{args.garmin_iface_ip}; relaying {args.gcv_ip} -> '
        f'{", ".join(f"{h}:{p}" for h, p in targets)}')

    pkts = bytes_ = dropped = 0
    last = time.monotonic()
    try:
        while not stop.is_set():
            try:
                payload, src = rx.recvfrom(65535)
            except socket.timeout:
                payload = None
            else:
                # Only relay the GCV's own imagery (ignore other group traffic).
                if src[0] == args.gcv_ip:
                    for tgt in targets:
                        try:
                            tx.sendto(payload, tgt)
                        except OSError:
                            dropped += 1
                    pkts += 1
                    bytes_ += len(payload)
            now = time.monotonic()
            if now - last >= 2.0:
                extra = f' ({dropped} dropped)' if dropped else ''
                log(f'imagery: {pkts} pkts / {bytes_:,} bytes relayed{extra}')
                last = now
    finally:
        rx.close()
        tx.close()


def main():
    parser = argparse.ArgumentParser(
        description='Garmin Marine Network proxy: relay GCV imagery + control '
                    'to a ROS host that cannot link the Garmin NIC.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--garmin-iface-ip', default='172.16.55.235',
                        help='local NIC IP on the Garmin Marine Network')
    parser.add_argument('--gcv-ip', default='172.16.3.0',
                        help='the GCV sidescan IP (control target / mcast source)')
    parser.add_argument('--control-port', type=int, default=50227)
    parser.add_argument('--mcast-group', default='239.254.2.1')
    parser.add_argument('--imagery-port', type=int, default=50220)
    parser.add_argument('--listen-ip', default='192.168.20.8',
                        help='local NIC IP facing the ROS host; set the driver '
                             "gcv_ip to this")
    parser.add_argument('--relay-to', nargs='+', default=['192.168.20.5'],
                        help='ROS host IP(s) to forward imagery to')
    parser.add_argument('--re-multicast', action='store_true',
                        help='also re-emit imagery as multicast on the ROS-host LAN')
    args = parser.parse_args()

    log('Garmin Marine Network proxy')
    log(f'  imagery : mcast {args.mcast_group}:{args.imagery_port} on '
        f'{args.garmin_iface_ip}  ->  unicast {args.imagery_port} to '
        f'{", ".join(args.relay_to)}'
        + (' (+ re-multicast)' if args.re_multicast else ''))
    log(f'  control : tcp {args.listen_ip}:{args.control_port}  ->  '
        f'{args.gcv_ip}:{args.control_port}')
    log(f'  drive gabby with:  gcv_ip:={args.listen_ip}  iface_ip:=<gabby LAN IP>')
    log('  Ctrl+C to stop.')

    stop = threading.Event()
    ctrl = threading.Thread(
        target=_control_loop,
        args=(args.listen_ip, args.control_port, args.gcv_ip, stop),
        daemon=True)
    ctrl.start()
    try:
        _imagery_loop(args, stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        log('stopped.')


if __name__ == '__main__':
    main()
