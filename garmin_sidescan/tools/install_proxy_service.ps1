<#
.SYNOPSIS
    Install garmin_marine_network_proxy.ps1 as a Windows service via NSSM.

.DESCRIPTION
    Registers the proxy (next to this script) as an auto-start Windows service so
    the GCV -> ROS-host relay runs unattended and survives reboots. The proxy's
    own defaults carry the network wiring (edit the proxy, or pass args via
    $ProxyArgs below, if they differ from your install).

    Run from an elevated (Administrator) PowerShell. Re-running reinstalls cleanly.

.PARAMETER Nssm
    Path to nssm.exe (default: 'nssm' on PATH). Install NSSM first, e.g.
    `winget install NSSM.NSSM` or unzip from https://nssm.cc and add to PATH.

.PARAMETER ProxyScript
    Path to garmin_marine_network_proxy.ps1 (default: the copy beside this script).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_proxy_service.ps1
#>
param(
    [string] $Nssm        = 'nssm',
    [string] $ServiceName = 'GarminProxy',
    [string] $ProxyScript = (Join-Path $PSScriptRoot 'garmin_marine_network_proxy.ps1'),
    [string] $ProxyArgs   = '',
    [string] $LogDir      = (Join-Path $env:ProgramData 'GarminProxy\logs')
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
if (-not (Test-Path $ProxyScript)) {
    throw "Proxy script not found: $ProxyScript"
}

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
# -File must come last on the powershell command line.
$appParams = "-NoProfile -ExecutionPolicy Bypass -File `"$ProxyScript`""
if ($ProxyArgs) { $appParams += " $ProxyArgs" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# --- reinstall cleanly ------------------------------------------------------ #
# Gate on Get-Service, not `nssm status`: on a fresh install nssm writes
# "Can't open service!" to stderr and exits non-zero, which trips
# $ErrorActionPreference = 'Stop' and aborts before anything is installed.
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "Existing '$ServiceName' service found; removing first..."
    & $Nssm stop   $ServiceName 2>$null | Out-Null
    & $Nssm remove $ServiceName confirm | Out-Null
}

Write-Host "Installing service '$ServiceName'..."
& $Nssm install $ServiceName $powershell $appParams

# Run from the proxy's dir; auto-start at boot; restart on any exit.
& $Nssm set $ServiceName AppDirectory   (Split-Path $ProxyScript)
& $Nssm set $ServiceName Start          SERVICE_AUTO_START
& $Nssm set $ServiceName AppExit Default Restart
& $Nssm set $ServiceName AppRestartDelay 2000
& $Nssm set $ServiceName AppStdout      (Join-Path $LogDir 'garmin_proxy.out.log')
& $Nssm set $ServiceName AppStderr      (Join-Path $LogDir 'garmin_proxy.err.log')
& $Nssm set $ServiceName AppRotateFiles 1
& $Nssm set $ServiceName AppRotateBytes 10485760
& $Nssm set $ServiceName Description `
    'Relays the Garmin GCV sidescan (Marine Network) to the ROS host for the garmin_sidescan driver.'

Write-Host "Starting '$ServiceName'..."
& $Nssm start $ServiceName
& $Nssm status $ServiceName
Write-Host "Done. Logs: $LogDir\garmin_proxy.*.log"
