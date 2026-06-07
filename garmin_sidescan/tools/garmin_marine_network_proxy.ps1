<#
.SYNOPSIS
    Garmin Marine Network proxy / relay.

.DESCRIPTION
    Bridges a Garmin GCV sidescan sonar (on the Garmin "Marine Network",
    172.16.0.0/16) to a ROS host (e.g. gabby) whose NIC will not link the
    Garmin PHY directly. Run this on a Windows host (mercat) that DOES link the
    Garmin (here: NIC "Ethernet 2", 172.16.55.235/16), with a second NIC on the
    same LAN as the ROS host (here: "BRIDGE", 192.168.20.8/24, gabby = .5).

    It relays the two channels the garmin_sidescan driver uses, exactly as the
    driver expects to see them, so the *unmodified* driver runs on gabby:

      * Imagery  : GCV UDP multicast 239.254.2.1:50220 (received on the Marine
                   Network NIC) -> forwarded as UNICAST UDP to the ROS host on
                   the same port. The relay socket is bound to -ListenIp so the
                   datagrams' source address is -ListenIp; set the driver's
                   gcv_ip to -ListenIp and its filter_src check passes.

      * Control  : TCP <ListenIp>:50227 (from the driver) -> GCV <GcvIp>:50227.
                   The driver opens one short connection per command frame and
                   serializes them, so the listener handles connections one at a
                   time. Bidirectional, though the GCV does not reply.

    There is NO host->GCV imagery path; the relay is one-way for UDP and
    on-demand for TCP. Nothing is sent to the GCV except the driver's own
    control frames, so the sonar's transmit safety (sound-speed watchdog) still
    lives entirely in the driver on gabby.

.PARAMETER GarminIfaceIp
    Local IP of the NIC on the Garmin Marine Network (the multicast-join iface).

.PARAMETER GcvIp
    The GCV sidescan's IP (control target / multicast source to accept).

.PARAMETER ListenIp
    Local IP of the NIC facing the ROS host. The TCP control port is bound here
    and the relayed imagery is sourced from here. Set the driver's gcv_ip to
    THIS value.

.PARAMETER RelayTo
    One or more ROS-host IPs to forward imagery to (default: gabby).

.PARAMETER ReMulticast
    Also re-emit imagery as multicast on the ListenIp side (in addition to the
    unicast forwards). Only useful if the ROS host genuinely joins the group on
    the shared LAN; unicast is the robust default and needs no switch IGMP.

.EXAMPLE
    # On mercat, with the discovered defaults:
    pwsh -File garmin_marine_network_proxy.ps1

.EXAMPLE
    # On gabby, run the stock driver pointed at the proxy:
    ros2 launch garmin_sidescan garmin_sidescan.launch.py `
        gcv_ip:=192.168.20.8 iface_ip:=192.168.20.5

.NOTES
    Stdlib/.NET only - no Python, no modules. PowerShell 5.1 or 7+.
    Companion to the garmin_sidescan ROS 2 driver (see ../README.md).
#>
[CmdletBinding()]
param(
    [string]   $GarminIfaceIp = '172.16.55.235',  # mercat NIC on the Marine Network
    [string]   $GcvIp         = '172.16.3.0',      # GCV-20 sidescan
    [int]      $ControlPort   = 50227,
    [string]   $McastGroup    = '239.254.2.1',
    [int]      $ImageryPort   = 50220,
    [string]   $ListenIp      = '192.168.20.8',    # mercat NIC facing gabby
    [string[]] $RelayTo       = @('192.168.20.5'), # gabby (ROS host)
    [switch]   $ReMulticast
)

$ErrorActionPreference = 'Stop'

function Log([string]$msg) {
    # [Console]::WriteLine writes straight to stdout so logs from the background
    # control-relay runspace interleave with the main imagery loop's output.
    [Console]::WriteLine(('{0:HH:mm:ss}  {1}' -f [DateTime]::Now, $msg))
}

Log "Garmin Marine Network proxy"
Log "  imagery : mcast ${McastGroup}:${ImageryPort} on ${GarminIfaceIp}  ->  unicast ${ImageryPort} to $($RelayTo -join ', ')$(if($ReMulticast){' (+ re-multicast)'})"
Log "  control : tcp ${ListenIp}:${ControlPort}  ->  ${GcvIp}:${ControlPort}"
Log "  drive gabby with:  gcv_ip:=${ListenIp}  iface_ip:=<gabby LAN IP>"
Log "  Ctrl+C to stop."

# --- background: TCP control relay -------------------------------------------
# Run on its own runspace so the (blocking) accept loop does not stall the
# imagery loop. The driver serializes its command sends, so sequential handling
# of one connection at a time is sufficient and matches client behaviour.
$controlRunspace = [runspacefactory]::CreateRunspace()
$controlRunspace.Open()
$controlPs = [powershell]::Create()
$controlPs.Runspace = $controlRunspace
[void]$controlPs.AddScript({
    param($ListenIp, $ControlPort, $GcvIp)

    function Log([string]$msg) {
        [Console]::WriteLine(('{0:HH:mm:ss}  {1}' -f [DateTime]::Now, $msg))
    }

    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Parse($ListenIp), $ControlPort)
    $listener.Start()
    Log "control: listening on ${ListenIp}:${ControlPort}"
    try {
        while ($true) {
            $client   = $listener.AcceptTcpClient()   # unblocks (throws) on Stop()
            $upstream = $null
            try {
                $client.NoDelay = $true
                $peer = $client.Client.RemoteEndPoint.ToString()
                $upstream = [System.Net.Sockets.TcpClient]::new()
                $upstream.Connect($GcvIp, $ControlPort)
                $upstream.NoDelay = $true

                $cs = $client.GetStream();  $cs.ReadTimeout = 4000
                $us = $upstream.GetStream(); $us.ReadTimeout = 4000

                # Pump both directions; the client (driver) sends its frames then
                # closes, which completes the up-copy and we tear the pair down.
                $up   = $cs.CopyToAsync($us)
                $down = $us.CopyToAsync($cs)
                [void][System.Threading.Tasks.Task]::WaitAny(@($up, $down))
                Log "control: $peer -> ${GcvIp}:${ControlPort}  (relayed)"
            } catch {
                Log "control: relay error: $($_.Exception.Message)"
            } finally {
                if ($client)   { $client.Close() }
                if ($upstream) { $upstream.Close() }
            }
        }
    } catch {
        # listener.Stop() during shutdown lands here; exit quietly.
    } finally {
        $listener.Stop()
    }
}).AddArgument($ListenIp).AddArgument($ControlPort).AddArgument($GcvIp)
$controlHandle = $controlPs.BeginInvoke()

# --- main: imagery multicast -> unicast relay --------------------------------
$rx = $null; $tx = $null
try {
    $rx = [System.Net.Sockets.Socket]::new(
        [System.Net.Sockets.AddressFamily]::InterNetwork,
        [System.Net.Sockets.SocketType]::Dgram,
        [System.Net.Sockets.ProtocolType]::Udp)
    $rx.SetSocketOption([System.Net.Sockets.SocketOptionLevel]::Socket,
                        [System.Net.Sockets.SocketOptionName]::ReuseAddress, $true)
    $rx.Bind([System.Net.IPEndPoint]::new([System.Net.IPAddress]::Any, $ImageryPort))
    $mreq = [System.Net.Sockets.MulticastOption]::new(
        [System.Net.IPAddress]::Parse($McastGroup),
        [System.Net.IPAddress]::Parse($GarminIfaceIp))
    $rx.SetSocketOption([System.Net.Sockets.SocketOptionLevel]::IP,
                        [System.Net.Sockets.SocketOptionName]::AddMembership, $mreq)
    $rx.ReceiveTimeout = 1000   # so the loop can heartbeat / honour Ctrl+C

    # Bind tx to ListenIp so the forwarded datagrams' SOURCE is ListenIp; the
    # driver's filter_src compares the source against gcv_ip (= ListenIp).
    $tx = [System.Net.Sockets.Socket]::new(
        [System.Net.Sockets.AddressFamily]::InterNetwork,
        [System.Net.Sockets.SocketType]::Dgram,
        [System.Net.Sockets.ProtocolType]::Udp)
    $tx.Bind([System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse($ListenIp), 0))

    $targets = [System.Collections.Generic.List[System.Net.EndPoint]]::new()
    foreach ($h in $RelayTo) {
        $targets.Add([System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse($h), $ImageryPort))
    }
    if ($ReMulticast) {
        $tx.SetSocketOption([System.Net.Sockets.SocketOptionLevel]::IP,
                            [System.Net.Sockets.SocketOptionName]::MulticastInterface,
                            [System.Net.IPAddress]::Parse($ListenIp).GetAddressBytes())
        $tx.SetSocketOption([System.Net.Sockets.SocketOptionLevel]::IP,
                            [System.Net.Sockets.SocketOptionName]::MulticastTimeToLive, 1)
        $targets.Add([System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse($McastGroup), $ImageryPort))
    }

    $buf = New-Object byte[] 65535
    $pkts = 0L; $bytes = 0L; $dropped = 0L
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Log "imagery: joined ${McastGroup}:${ImageryPort} on ${GarminIfaceIp}; relaying from ${GcvIp}"

    while ($true) {
        [System.Net.EndPoint]$src = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Any, 0)
        try {
            $n = $rx.ReceiveFrom($buf, [ref]$src)
        } catch [System.Net.Sockets.SocketException] {
            # ReceiveTimeout -> heartbeat and keep going.
            if ($sw.Elapsed.TotalSeconds -ge 2) {
                Log ("imagery: {0} pkts / {1:n0} bytes relayed{2}" -f $pkts, $bytes,
                     $(if ($dropped) { " ($dropped dropped)" } else { '' }))
                $sw.Restart()
            }
            continue
        }
        # Only relay the GCV's own imagery (ignore other Marine Network multicast).
        if (([System.Net.IPEndPoint]$src).Address.ToString() -ne $GcvIp) { continue }

        foreach ($t in $targets) {
            try {
                [void]$tx.SendTo($buf, 0, $n, [System.Net.Sockets.SocketFlags]::None, $t)
            } catch {
                $dropped++
            }
        }
        $pkts++; $bytes += $n
        if ($sw.Elapsed.TotalSeconds -ge 2) {
            Log ("imagery: {0} pkts / {1:n0} bytes relayed{2}" -f $pkts, $bytes,
                 $(if ($dropped) { " ($dropped dropped)" } else { '' }))
            $sw.Restart()
        }
    }
} finally {
    Log "shutting down..."
    if ($rx) { $rx.Close() }
    if ($tx) { $tx.Close() }
    # Stop() unblocks the runspace's AcceptTcpClient so it can exit.
    try { $controlPs.Stop() } catch {}
    try { $controlPs.Dispose() } catch {}
    try { $controlRunspace.Dispose() } catch {}
    Log "stopped."
}
