<#
.SYNOPSIS
    Install m3_zda_udp_relay.ps1 as a Windows service via NSSM.

.DESCRIPTION
    Registers the relay (next to this script) as an auto-start Windows service so
    the serial-ZDA -> M3-head UDP feed runs unattended and survives reboots. The
    relay's own defaults carry the wiring (COM1 @ 9600 -> head 192.168.1.234:31100
    out the isolated NIC 192.168.1.8); edit the relay, or pass args via
    $RelayArgs below, if they differ from your install.

    Because the relay OWNS the serial port exclusively, do not run this service
    while the M3 topside software is also reading the same COM port.

    Run from an elevated (Administrator) PowerShell. Re-running reinstalls cleanly.
    Models the garmin_sidescan install_proxy_service.ps1.

.PARAMETER Nssm
    Path to nssm.exe (default: 'nssm' on PATH). Install NSSM first, e.g.
    `winget install NSSM.NSSM` or unzip from https://nssm.cc and add to PATH.

.PARAMETER RelayScript
    Path to m3_zda_udp_relay.ps1 (default: the copy beside this script).

.PARAMETER RelayArgs
    Extra arguments passed through to the relay, e.g. '-ComPort COM5 -BaudRate 4800'.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_zda_relay_service.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_zda_relay_service.ps1 `
        -RelayArgs '-ComPort COM5 -MinIntervalMs 900'
#>
param(
    [string] $Nssm        = 'nssm',
    [string] $ServiceName = 'M3ZdaRelay',
    [string] $RelayScript = (Join-Path $PSScriptRoot 'm3_zda_udp_relay.ps1'),
    [string] $RelayArgs   = '',
    [string] $LogDir      = (Join-Path $env:ProgramData 'M3ZdaRelay\logs')
)

$ErrorActionPreference = 'Stop'

# --- preconditions ---------------------------------------------------------- #
$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    throw 'Run this from an elevated (Administrator) PowerShell.'
}
if (-not (Get-Command $Nssm -ErrorAction SilentlyContinue)) {
    throw "nssm not found ('$Nssm'). Install it (winget install NSSM.NSSM) or pass -Nssm <path>."
}
if (-not (Test-Path $RelayScript)) {
    throw "Relay script not found: $RelayScript"
}

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
# -File must come last on the powershell command line.
$appParams = "-NoProfile -ExecutionPolicy Bypass -File `"$RelayScript`""
if ($RelayArgs) { $appParams += " $RelayArgs" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# --- reinstall cleanly ------------------------------------------------------ #
# Gate on Get-Service, not `nssm status`: on a fresh install nssm writes
# "Can't open service!" to stderr and exits non-zero, which trips
# $ErrorActionPreference = 'Stop' and aborts before anything is installed.
if (Get-Service -Name ([System.Management.Automation.WildcardPattern]::Escape($ServiceName)) -ErrorAction SilentlyContinue) {
    Write-Host "Existing '$ServiceName' service found; removing first..."
    & $Nssm stop   $ServiceName 2>$null | Out-Null
    & $Nssm remove $ServiceName confirm | Out-Null
}

Write-Host "Installing service '$ServiceName'..."
& $Nssm install $ServiceName $powershell $appParams
# PS 5.1: $ErrorActionPreference does not fail native commands -- gate on the
# exit code or a failed install barrels through every `nssm set` to "Done".
if ($LASTEXITCODE -ne 0) {
    throw "nssm install failed (exit $LASTEXITCODE) -- service not configured."
}

# Run from the relay's dir; auto-start at boot; restart on any exit.
& $Nssm set $ServiceName AppDirectory   (Split-Path $RelayScript)
& $Nssm set $ServiceName Start          SERVICE_AUTO_START
& $Nssm set $ServiceName AppExit Default Restart
& $Nssm set $ServiceName AppRestartDelay 2000
& $Nssm set $ServiceName AppStdout      (Join-Path $LogDir 'm3_zda_relay.out.log')
& $Nssm set $ServiceName AppStderr      (Join-Path $LogDir 'm3_zda_relay.err.log')
& $Nssm set $ServiceName AppRotateFiles 1
& $Nssm set $ServiceName AppRotateBytes 10485760
& $Nssm set $ServiceName Description `
    'Relays serial NMEA $--ZDA to the M3 sonar head over UDP (31100) for 1PPS time sync when QINSy is not running.'

Write-Host "Starting '$ServiceName'..."
& $Nssm start $ServiceName
& $Nssm status $ServiceName
Write-Host "Done. Logs: $LogDir\m3_zda_relay.*.log"
