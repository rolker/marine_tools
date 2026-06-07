# garmin_sidescan/tools — Marine Network proxy

The Garmin GCV sidescan sits on the Garmin **Marine Network** (172.16.0.0/16). A
ROS host whose NIC won't link the Garmin PHY can't reach it directly, so run the
proxy on a host that *is* on the Marine Network and also on the ROS host's LAN
(e.g. mercat), and the unmodified `garmin_sidescan` driver runs on the ROS host
(e.g. gabby).

```
GCV ──Marine Network (172.16.x)──> proxy host ──> ROS-host LAN ──> garmin_sidescan
     239.254.2.1:50220 imagery                    unicast :50220 imagery
     TCP :50227 control                           TCP <listen-ip>:50227 control
```

| File | Purpose |
|------|---------|
| `garmin_marine_network_proxy.py` | The relay (stdlib Python). |
| `garmin_marine_network_proxy.ps1` | Same relay in PowerShell, for a Windows proxy host. |
| `install_proxy_service.ps1` | Register the `.ps1` proxy as an auto-start Windows service (NSSM). |

On the ROS host, point the driver at the proxy (its `--listen-ip` is the imagery
source address, so set `gcv_ip` to it):

```bash
ros2 launch garmin_sidescan garmin_sidescan.launch.py \
    gcv_ip:=<proxy listen-ip> iface_ip:=<ROS-host NIC>
```

## Run the proxy as a Windows service (NSSM)

On the proxy host, from an **elevated** PowerShell:

```powershell
winget install NSSM.NSSM          # or unzip nssm.exe from https://nssm.cc onto PATH
powershell -ExecutionPolicy Bypass -File .\install_proxy_service.ps1
```

This registers a `GarminProxy` service (auto-start at boot, restart on exit) that
runs `garmin_marine_network_proxy.ps1` with its built-in defaults. Override the
proxy's network parameters by editing the proxy's `param()` block or passing
`-ProxyArgs '...'` to the installer. Logs rotate in `C:\project11\logs\` (set
`-LogDir`).

```powershell
nssm status  GarminProxy          # SERVICE_RUNNING
nssm restart GarminProxy
nssm remove  GarminProxy confirm  # uninstall
```

> The proxy's default parameters (`-GcvIp 172.16.3.0`, `-ListenIp 192.168.20.8`,
> relay to `192.168.20.5`) are the BizzyBoat/mercat→gabby wiring; change them for
> a different install.
