#!/usr/bin/env python3
"""
Garmin Marine Network proxy / relay.

Bridges a Garmin GCV sidescan sonar (on the Garmin "Marine Network",
172.16.0.0/16) to a ROS host (e.g. gabby) whose NIC will not link the Garmin
PHY directly. Run this on a host that DOES link the Garmin (here: mercat's
"Ethernet 2", 172.16.55.235/16) and also has a NIC on the ROS host's LAN
(here: "BRIDGE", 192.168.20.8/24, gabby = 192.168.20.5).

It relays the channels the ``garmin_sidescan`` driver uses, exactly as the
driver expects them, so the *unmodified* driver runs on gabby:

* **Imagery** - GCV UDP multicast ``239.254.2.1:50220`` (received on the Marine
  Network NIC) is forwarded as **unicast** UDP to the ROS host on the same port.
  The relay's send socket is bound to ``--listen-ip`` so each datagram's source
  address is ``--listen-ip``; set the driver's ``gcv_ip`` to ``--listen-ip`` and
  its ``filter_src`` check passes. (``--re-multicast`` additionally re-emits the
  group on the ROS-host LAN, for hosts that genuinely join it there.)

* **Status** - GCV ``8e03`` multicast ``239.254.2.2:50050`` (transmit flag +
  nadir depth) is relayed the same way, filtered to the GCV's own source.
  Disable with ``--no-status``.

* **Config** - chartplotter CDP multicast ``239.254.2.11:51000`` (carries the
  active range under auto-range) is relayed from *every* source on the group --
  it originates at the chartplotter, not the GCV. Capture-only; the driver does
  not parse it. Disable with ``--no-config``.

* **Control** - TCP ``<listen-ip>:50227`` from the driver is forwarded to the
  GCV at ``<gcv-ip>:50227``. The driver opens one short connection per command
  and serializes them; connections are handled on a thread each regardless.

Every UDP relay is one-way GCV->ROS and nothing reaches the GCV except the
driver's own control frames, so all sonar transmit control stays entirely in
the driver on gabby. The status/config streams are
relayed by default so the driver's ``debug_raw`` capture records them; relaying
status also feeds the driver's (otherwise-starved) device transmit-flag path.

On gabby, run the stock driver pointed at the proxy::

    ros2 launch garmin_sidescan garmin_sidescan.launch.py \
        gcv_ip:=192.168.20.8 iface_ip:=192.168.20.5

Stdlib only - no third-party packages. Companion to the garmin_sidescan ROS 2
driver (see ../README.md).
"""
import argparse
import socket
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
# UDP relay: multicast <group>:<port> (Marine Network)  ->  unicast to ROS host
# --------------------------------------------------------------------------- #
def _open_mcast_rx(iface_ip, group, port):
    """Join a GCV multicast group on a specific local interface."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', port))
    mreq = socket.inet_aton(group) + socket.inet_aton(iface_ip)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(1.0)
    return sock


def _relay_loop(args, stop, name, group, port, src_filter,
                re_multicast=False, log_period=2.0):
    """
    Relay one GCV multicast stream to the ROS host as unicast.

    Receive ``group:port`` on the Marine-Network NIC and forward each datagram
    to every ``--relay-to`` host on the same port, with the send socket bound to
    ``--listen-ip`` so the source address matches the driver's ``gcv_ip``.  When
    ``src_filter`` is an IP, only datagrams from that source are relayed (imagery
    and the ``8e03`` status come from the GCV itself, so other devices sharing
    the group are ignored); pass ``None`` to relay every source -- the CDP config
    originates at the chartplotter, whose IP is not ours to assume.  This stays a
    one-way GCV->ROS path: nothing it relays can reach the GCV.  ``re_multicast``
    additionally re-emits the group on the ROS-host LAN for hosts that genuinely
    join it there.
    """
    rx = _open_mcast_rx(args.garmin_iface_ip, group, port)

    # Bind tx to listen_ip so forwarded datagrams' SOURCE is listen_ip; the
    # driver's filter_src compares the source against gcv_ip (= listen_ip).
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.bind((args.listen_ip, 0))

    targets = [(h, port) for h in args.relay_to]
    if re_multicast:
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                      socket.inet_aton(args.listen_ip))
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        targets.append((group, port))

    log(f'{name}: joined {group}:{port} on {args.garmin_iface_ip}; relaying '
        f'{src_filter or "any-source"} -> '
        f'{", ".join(f"{h}:{p}" for h, p in targets)}')

    pkts = bytes_ = dropped = 0
    logged = 0
    last = time.monotonic()
    try:
        while not stop.is_set():
            try:
                payload, src = rx.recvfrom(65535)
            except socket.timeout:
                payload = None
            else:
                if src_filter is None or src[0] == src_filter:
                    for tgt in targets:
                        try:
                            tx.sendto(payload, tgt)
                        except OSError:
                            dropped += 1
                    pkts += 1
                    bytes_ += len(payload)
            now = time.monotonic()
            # Report throughput periodically, but only when something moved, so
            # the low-rate status/config streams don't spam idle lines.
            if now - last >= log_period and pkts != logged:
                extra = f' ({dropped} dropped)' if dropped else ''
                log(f'{name}: {pkts} pkts / {bytes_:,} bytes relayed{extra}')
                logged = pkts
                last = now
    finally:
        rx.close()
        tx.close()


def main():
    """Parse arguments and run the imagery/status/config/control relays."""
    parser = argparse.ArgumentParser(
        description='Garmin Marine Network proxy: relay GCV imagery + status + '
                    'config + control to a ROS host that cannot link the Garmin NIC.',
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
                             'gcv_ip to this')
    parser.add_argument('--relay-to', nargs='+', default=['192.168.20.5'],
                        help='ROS host IP(s) to forward imagery/status/config to')
    parser.add_argument('--re-multicast', action='store_true',
                        help='also re-emit imagery as multicast on the ROS-host LAN')
    # Status + config streams: relayed by default so the driver's debug_raw
    # capture sees them (they carry the device transmit flag / depth and the
    # chartplotter's CDP range config). Disable per-stream if a capture-only or
    # control-untouched run is wanted.
    parser.add_argument('--status-group', default='239.254.2.2',
                        help='GCV 8e03 status multicast group (tx flag + depth)')
    parser.add_argument('--status-port', type=int, default=50050)
    parser.add_argument('--no-status', action='store_true',
                        help='do not relay the :50050 status stream')
    parser.add_argument('--config-group', default='239.254.2.11',
                        help='chartplotter CDP config multicast group (range)')
    parser.add_argument('--config-port', type=int, default=51000)
    parser.add_argument('--no-config', action='store_true',
                        help='do not relay the :51000 config stream')
    args = parser.parse_args()

    log('Garmin Marine Network proxy')
    log(f'  imagery : mcast {args.mcast_group}:{args.imagery_port} on '
        f'{args.garmin_iface_ip}  ->  unicast {args.imagery_port} to '
        f'{", ".join(args.relay_to)}'
        + (' (+ re-multicast)' if args.re_multicast else ''))
    if not args.no_status:
        log(f'  status  : mcast {args.status_group}:{args.status_port}  ->  '
            f'unicast {args.status_port} to {", ".join(args.relay_to)}')
    if not args.no_config:
        log(f'  config  : mcast {args.config_group}:{args.config_port}  ->  '
            f'unicast {args.config_port} to {", ".join(args.relay_to)}')
    log(f'  control : tcp {args.listen_ip}:{args.control_port}  ->  '
        f'{args.gcv_ip}:{args.control_port}')
    log(f'  drive gabby with:  gcv_ip:={args.listen_ip}  iface_ip:=<gabby LAN IP>')
    log('  Ctrl+C to stop.')

    stop = threading.Event()
    threading.Thread(
        target=_control_loop,
        args=(args.listen_ip, args.control_port, args.gcv_ip, stop),
        daemon=True).start()
    # Status comes from the GCV itself (filter to its IP so the chartplotter's
    # same-magic status frame isn't relayed too); config comes from the
    # chartplotter, so relay every source on that group.
    if not args.no_status:
        threading.Thread(
            target=_relay_loop, daemon=True,
            args=(args, stop, 'status', args.status_group, args.status_port,
                  args.gcv_ip),
            kwargs={'log_period': 10.0}).start()
    if not args.no_config:
        threading.Thread(
            target=_relay_loop, daemon=True,
            args=(args, stop, 'config', args.config_group, args.config_port,
                  None),
            kwargs={'log_period': 10.0}).start()
    try:
        _relay_loop(args, stop, 'imagery', args.mcast_group, args.imagery_port,
                    args.gcv_ip, re_multicast=args.re_multicast)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        log('stopped.')


if __name__ == '__main__':
    main()
