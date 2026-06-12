# garmin_sidescan/tools

## `sidescan_waterfall.py` — offline waterfall image (QA)

Renders a two-panel waterfall (down-look depth-corrected on top, side-scan
port|starboard below, shared time axis) from a bag, using the **same decoder as
the driver** (`garmin_sidescan.decode`). Self-contained — needs only the sidescan
`debug/raw` topic (record with `debug_raw:=true`); no external nav/sonar. The
metres-per-sample scale is self-derived per channel from the device's per-ping
**display-range varint** (sub-header `v2`); no hard-coded calibration. Sample
values are shown raw on a single global brightness scale (no per-ping
manipulation). Useful to eyeball decode quality (banding, range steps, bottom
tracking) on any capture.

```bash
python3 tools/sidescan_waterfall.py BAG --start 100 --end 620 --out wf.png
```

Needs a sourced workspace (for `garmin_sidescan` on the path) plus numpy +
matplotlib.

## `verify_range_scale.py` — interactive range-scale + nadir-depth check

Plots, from a `debug/raw` bag on a shared time axis: each channel's per-ping
sub-header **v2 display range** (the scale the driver publishes via
`sample_rate`, issue #35) with byte-13 bracket transitions marked, the
down-look **v1 bottom range** (published as `nadir_depth`, issue #16), and —
for before/after comparison — the implied range actually recorded in the bag's
`sonar_image_*` messages plus the `state` topic's commanded-range mirror.
Interactive matplotlib window by default (zoom into the auto-range
transitions); `--out FILE` renders a PNG headless.

```bash
python3 tools/verify_range_scale.py BAG [--start S --end S] [--out check.png]
```

## `replay_debug_raw.py` — replay a bag onto the imagery multicast

Hardware-free end-to-end driver testing: re-sends a bag's `debug/raw`
datagrams to the GCV multicast group, paced by bag timestamps, so the **actual
driver node** (socket layer up) processes a real capture live. Defaults to
loopback (`--iface-ip 127.0.0.1`, TTL 0) so nothing leaves the host.

```bash
# terminal 1 — the driver under test
ros2 run garmin_sidescan garmin_sidescan --ros-args \
    -p iface_ip:=127.0.0.1 -p filter_src:=false -p require_sound_speed:=false
# terminal 2 — the replay
python3 tools/replay_debug_raw.py BAG --start 100 --end 300
# terminal 3 — watch the output (sensor topics are best-effort; a
# default-reliable echo silently shows nothing)
ros2 topic echo --qos-reliability best_effort /garmin_sidescan/nadir_depth
```

---

# Marine Network proxy

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
`-ProxyArgs '...'` to the installer. Logs rotate in
`%ProgramData%\GarminProxy\logs\` (set `-LogDir`).

```powershell
nssm status  GarminProxy          # SERVICE_RUNNING
nssm restart GarminProxy
nssm remove  GarminProxy confirm  # uninstall
```

> The proxy's default parameters (`-GcvIp 172.16.3.0`, `-ListenIp 192.168.20.8`,
> relay to `192.168.20.5`) are the BizzyBoat/mercat→gabby wiring; change them for
> a different install.
