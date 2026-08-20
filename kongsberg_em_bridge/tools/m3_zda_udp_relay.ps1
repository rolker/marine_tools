<#
.SYNOPSIS
    Relay NMEA $--ZDA time/date sentences from a serial port to the M3 sonar
    HEAD over UDP, for 1PPS time synchronisation when QINSy is not running.

.DESCRIPTION
    The Kongsberg/Mesotech M3 sonar HEAD locks its ping clock to a hardware
    1PPS edge paired with an NMEA $--ZDA feed delivered *to the head* over UDP
    (default 192.168.1.234:31100 @ 1 Hz). Per the M3 install manual this ZDA
    must reach the HEAD, NOT the M3 topside software -- feeding the software
    (e.g. over serial) shows a green SYNC OK but does not drive head-level
    1PPS pairing.

    QINSy normally sources that UDP ZDA feed (its "Network NMEA ZDA (UDP) - 18"
    output driver). We do not always run QINSy, so this script provides the
    same feed from the serial ZDA that already arrives on mercat COM1
    (gabby COM3 -> mercat COM1, GP talker @ 9600 by default). It reads ZDA
    lines from the serial port, validates them, and forwards each as a UDP
    datagram to the head.

    Runs on the Windows host wired to the M3's isolated link (mercat: boat LAN
    192.168.20.8 + isolated NIC 192.168.1.8 -> head 192.168.1.234). The relay
    OWNS the serial port exclusively -- Windows COM ports are single-access, so
    the M3 topside software must not also be reading COM1 while this runs.

    Companion to the kongsberg_em_bridge ROS 2 driver (M3 .all decode). Models
    the garmin_sidescan proxy pattern (../../garmin_sidescan/tools/
    garmin_marine_network_proxy.ps1): .NET stdlib only, no Python, no modules.

.PARAMETER ComPort
    Serial port the ZDA arrives on (default COM1).

.PARAMETER BaudRate
    Serial baud; must match the ZDA source (zda_serial_bridge default is 9600).

.PARAMETER HeadIp
    M3 sonar HEAD IP -- the UDP destination (Kongsberg factory default
    192.168.1.234).

.PARAMETER HeadPort
    M3 head ZDA UDP port (default 31100, per the M3 manual).

.PARAMETER SourceIp
    Local IP to bind the UDP sender to, forcing egress out the M3-isolated NIC
    (mercat 192.168.1.8). Set '' to let the OS choose the interface.

.PARAMETER AllSentences
    Forward every valid NMEA sentence, not just $--ZDA. Off by default; the
    head only needs ZDA.

.PARAMETER NoChecksum
    Forward sentences without validating the NMEA XOR checksum. Off by default;
    corrupt lines are dropped so garbage never reaches the head.

.PARAMETER MinIntervalMs
    Minimum spacing between forwarded ZDA, in milliseconds (rate limit). 0
    (default) forwards every ZDA as received -- set the source to 1 Hz. Use
    e.g. 900 to thin a faster source down toward 1 Hz.

.PARAMETER ReconnectDelaySec
    Seconds to wait before reopening the serial port after an error/unplug.

.PARAMETER StaleWarnSec
    Warn if no valid ZDA has been forwarded within this many seconds.

.EXAMPLE
    # On mercat, with the discovered defaults (COM1 -> head 192.168.1.234:31100):
    pwsh -File m3_zda_udp_relay.ps1

.EXAMPLE
    # Different port/baud, thin a 5 Hz source toward 1 Hz:
    pwsh -File m3_zda_udp_relay.ps1 -ComPort COM5 -BaudRate 4800 -MinIntervalMs 900

.NOTES
    Stdlib/.NET only - no Python, no modules. PowerShell 5.1 or 7+.
    On the head: set Time Sync Mode = 1PPS (Device Properties -> Sonar Setup),
    and confirm head firmware >= 1.5. A green SYNC OK in the M3 software is not
    proof the head is 1PPS-locked -- verify via the head's Output Messages.
#>
[CmdletBinding()]
param(
    [string] $ComPort           = 'COM1',
    [int]    $BaudRate          = 9600,
    [string] $HeadIp            = '192.168.1.234',
    [int]    $HeadPort          = 31100,
    [string] $SourceIp          = '192.168.1.8',
    [switch] $AllSentences,
    [switch] $NoChecksum,
    [int]    $MinIntervalMs     = 0,
    [int]    $ReconnectDelaySec = 5,
    [int]    $StaleWarnSec      = 5
)

$ErrorActionPreference = 'Stop'

function Log([string]$msg) {
    [Console]::WriteLine(('{0:HH:mm:ss}  {1}' -f [DateTime]::Now, $msg))
}

# XOR checksum over the sentence body (between '$' and '*'), compared to the
# two hex digits after '*'. Returns $false when no checksum is present so
# corrupt/partial lines are dropped rather than forwarded to the head.
function Test-NmeaChecksum([string]$s) {
    $star = $s.IndexOf('*')
    if ($star -lt 2) { return $false }               # need "$X..*"
    $body  = $s.Substring(1, $star - 1)
    $given = $s.Substring($star + 1)
    if ($given.Length -lt 2) { return $false }
    $x = 0
    foreach ($ch in $body.ToCharArray()) { $x = $x -bxor [int][char]$ch }
    return ($given.Substring(0, 2).ToUpperInvariant() -eq ('{0:X2}' -f $x))
}

Log "M3 ZDA -> head UDP relay"
Log "  serial  : ${ComPort} @ ${BaudRate} 8-N-1  (relay owns the port)"
Log "  forward : $(if ($AllSentences) { 'all valid NMEA' } else { '$--ZDA only' })$(if ($NoChecksum) { ' (checksum NOT verified)' } else { '' })$(if ($MinIntervalMs -gt 0) { " (>= ${MinIntervalMs} ms apart)" } else { '' })"
Log "  head    : udp -> ${HeadIp}:${HeadPort}$(if ($SourceIp) { "  (source ${SourceIp})" } else { '' })"
Log "  Ctrl+C to stop."

# --- UDP sender: bind to SourceIp so datagrams egress the isolated NIC -------
$tx = $null
try {
    if ($SourceIp) {
        $tx = [System.Net.Sockets.UdpClient]::new(
            [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse($SourceIp), 0))
    } else {
        $tx = [System.Net.Sockets.UdpClient]::new()
    }
} catch {
    Log "FATAL: cannot bind UDP sender to '${SourceIp}': $($_.Exception.Message)"
    Log "       (is the M3-isolated NIC up with that IP? pass -SourceIp '' to let the OS choose.)"
    throw
}

$sent = 0L; $bad = 0L; $dropped = 0L
$lastSentTicks = 0L                                  # for MinIntervalMs throttle
$lastGood      = [DateTime]::Now
$staleWarned   = $false
$statSw        = [System.Diagnostics.Stopwatch]::StartNew()

# --- reconnect loop: reopen the serial port on unplug / error ----------------
try {
    while ($true) {
        $port = $null
        try {
            $port = [System.IO.Ports.SerialPort]::new($ComPort, $BaudRate)
            $port.Parity      = [System.IO.Ports.Parity]::None
            $port.DataBits    = 8
            $port.StopBits    = [System.IO.Ports.StopBits]::One
            $port.NewLine     = "`n"                 # NMEA is CR/LF; strip CR after read
            $port.ReadTimeout = 1500                 # so the loop can heartbeat / stale-check
            $port.Open()
            Log "opened ${ComPort} @ ${BaudRate}"
        } catch {
            Log "WARN: cannot open ${ComPort}: $($_.Exception.Message); retry in ${ReconnectDelaySec}s"
            if ($port) { try { $port.Dispose() } catch {} }
            Start-Sleep -Seconds $ReconnectDelaySec
            continue
        }

        try {
            while ($true) {
                $line = $null
                try {
                    $line = $port.ReadLine()
                } catch [TimeoutException] {
                    # No data this window -- heartbeat, and warn if ZDA has gone stale.
                    if (-not $staleWarned -and
                        (([DateTime]::Now - $lastGood).TotalSeconds -ge $StaleWarnSec)) {
                        Log ("WARN: no valid ZDA forwarded in {0:n0}s (source down? wrong baud/port?)" -f `
                             ([DateTime]::Now - $lastGood).TotalSeconds)
                        $staleWarned = $true
                    }
                    if ($statSw.Elapsed.TotalSeconds -ge 10) {
                        Log ("stats: {0} sent, {1} bad-cksum, {2} udp-dropped" -f $sent, $bad, $dropped)
                        $statSw.Restart()
                    }
                    continue
                }

                if ($null -eq $line) { continue }
                $line = $line.TrimEnd("`r", "`n").Trim()
                if ($line.Length -eq 0) { continue }

                if (-not $AllSentences -and $line -notmatch '^\$..ZDA,') { continue }
                if (-not $NoChecksum -and -not (Test-NmeaChecksum $line)) { $bad++; continue }

                if ($MinIntervalMs -gt 0) {
                    $nowTicks = [DateTime]::UtcNow.Ticks
                    if ($lastSentTicks -ne 0 -and
                        (($nowTicks - $lastSentTicks) / 10000) -lt $MinIntervalMs) {
                        continue                     # too soon since last forward
                    }
                    $lastSentTicks = $nowTicks
                }

                # Reconstruct the full NMEA sentence (trailing CR/LF) for the head.
                $bytes = [System.Text.Encoding]::ASCII.GetBytes($line + "`r`n")
                try {
                    [void]$tx.Send($bytes, $bytes.Length, $HeadIp, $HeadPort)
                    $sent++
                    $lastGood = [DateTime]::Now
                    if ($staleWarned) { Log "ZDA feed recovered"; $staleWarned = $false }
                } catch {
                    $dropped++
                    if (($dropped % 50) -eq 1) {
                        Log "WARN: UDP send to ${HeadIp}:${HeadPort} failed: $($_.Exception.Message)"
                    }
                }

                if ($statSw.Elapsed.TotalSeconds -ge 10) {
                    Log ("stats: {0} sent, {1} bad-cksum, {2} udp-dropped" -f $sent, $bad, $dropped)
                    $statSw.Restart()
                }
            }
        } catch {
            Log "serial error on ${ComPort}: $($_.Exception.Message); reopening in ${ReconnectDelaySec}s"
            Start-Sleep -Seconds $ReconnectDelaySec
        } finally {
            if ($port) {
                try { if ($port.IsOpen) { $port.Close() } } catch {}
                try { $port.Dispose() } catch {}
            }
        }
    }
} finally {
    Log "shutting down..."
    if ($tx) { try { $tx.Close() } catch {} }
    Log ("stopped. totals: {0} sent, {1} bad-cksum, {2} udp-dropped" -f $sent, $bad, $dropped)
}
