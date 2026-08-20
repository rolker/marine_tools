# kongsberg_em_bridge/tools

# M3 head ZDA → UDP relay

The Kongsberg/Mesotech **M3** sonar head locks its ping clock to a hardware
**1PPS** edge paired with an NMEA **`$--ZDA`** feed delivered *to the head* over
UDP (`192.168.1.234:31100` @ 1 Hz). Per the M3 install manual this ZDA must
reach the **head**, not the M3 topside software — feeding the software (e.g.
over serial) shows a green *SYNC OK* but does **not** drive head-level 1PPS
pairing.

**QINSy** normally sources that UDP feed (its *Network NMEA ZDA (UDP) - 18*
output driver). When QINSy is not running, this relay provides the same feed
from the serial ZDA already arriving on **mercat COM1** (gabby COM3 → mercat
COM1, `GP` talker @ 9600). It reads ZDA from the serial port, validates the NMEA
checksum, and forwards each sentence as a UDP datagram to the head.

```
gabby COM3 ──null-modem──> mercat COM1 ──[this relay]──> M3 head
   $--ZDA @ 1 Hz              (owns COM1)   UDP 192.168.1.234:31100
                                            source NIC 192.168.1.8 (isolated link)
```

Runs on the Windows host wired to the M3's isolated link (mercat: boat LAN
`192.168.20.8` + isolated NIC `192.168.1.8`). Modeled on the
`garmin_sidescan` Marine-Network proxy (`.NET` stdlib only, no Python).

| File | Purpose |
|------|---------|
| `m3_zda_udp_relay.ps1` | The relay: serial `$--ZDA` → UDP to the M3 head (PowerShell, .NET only). |
| `install_zda_relay_service.ps1` | Register the relay as an auto-start Windows service (NSSM). |

## Prerequisites (on the head)

- **Time Sync Mode = 1PPS**: `Device Properties → Sonar Setup → Time Sync Mode`.
- **Head firmware ≥ 1.5** (hard prerequisite for 1PPS mode).
- 1PPS hardware edge wired to the head breakout (separate from this ZDA feed).
- A green *SYNC OK* in the M3 software is **not** proof of head 1PPS lock —
  verify via the head's *Output Messages*.

## ⚠ The relay owns the serial port

Windows COM ports are single-access. This relay opens COM1 **exclusively**, so
the M3 topside software (or QINSy) must not also be reading that port while the
relay runs. Use this when QINSy is off and nothing else is bound to COM1.

## Run it

```powershell
# defaults: COM1 @ 9600 -> 192.168.1.234:31100 out source NIC 192.168.1.8
pwsh -File tools\m3_zda_udp_relay.ps1

# override port/baud, or thin a faster source toward 1 Hz
pwsh -File tools\m3_zda_udp_relay.ps1 -ComPort COM5 -BaudRate 4800 -MinIntervalMs 900
```

Key parameters (see `Get-Help .\m3_zda_udp_relay.ps1 -Full`): `-ComPort`,
`-BaudRate`, `-HeadIp`, `-HeadPort`, `-SourceIp` (`''` to let the OS pick the
NIC), `-AllSentences`, `-NoChecksum`, `-MinIntervalMs`, `-ReconnectDelaySec`,
`-StaleWarnSec`. It reconnects on serial unplug/error and warns when no valid
ZDA has been forwarded within `-StaleWarnSec`.

## Run as a Windows service (NSSM)

On mercat, from an **elevated** PowerShell:

```powershell
winget install NSSM.NSSM          # or unzip nssm.exe from https://nssm.cc onto PATH
powershell -ExecutionPolicy Bypass -File .\install_zda_relay_service.ps1
```

This registers an `M3ZdaRelay` service (auto-start at boot, restart on exit)
running the relay with its built-in defaults. Override by editing the relay's
`param()` block or passing `-RelayArgs '...'` to the installer. Logs rotate in
`%ProgramData%\M3ZdaRelay\logs\` (set `-LogDir`).

```powershell
nssm status  M3ZdaRelay           # SERVICE_RUNNING
nssm restart M3ZdaRelay
nssm remove  M3ZdaRelay confirm    # uninstall
```

> The defaults (`-HeadIp 192.168.1.234`, `-HeadPort 31100`, `-SourceIp
> 192.168.1.8`, `-ComPort COM1 @ 9600`) are the BizzyBoat/mercat wiring; change
> them for a different install.

## Verify the feed

```powershell
# is the datagram leaving mercat toward the head?
pktmon / Wireshark on the isolated NIC, filter udp.port == 31100

# on the head: Output Messages should show incoming ZDA @ 1 Hz, and the
# 1PPS pulse count should read ~10 per 10 s once Time Sync Mode = 1PPS.
```
