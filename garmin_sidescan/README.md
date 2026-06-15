# garmin_sidescan

ROS 2 driver for the Garmin GCV-10 / GCV-20 sidescan sonar (Marine Network).
Decodes the imagery multicast into `marine_acoustic_msgs/RawSonarImage` and owns
transmit/range control over the GCV TCP command port. (The GCV stops pinging on
its own when out of the water, so no external sound-speed interlock is needed.)

The chartplotter is required only to power up / wake the Marine Network; it is
otherwise inaccessible, so **this node performs all sonar control.**

## Network & data streams

Garmin Marine Network: a flat `172.16.0.0/16` LAN; devices self-assign IPs (last
two IP octets mirror the MAC). Every frame is `<2-byte magic> 00 00` +
LE-length(4) + payload. The GCV **will not run without a Garmin chartplotter**
present — the master asserts a hardware enable (not Wake-on-LAN) and a ~1 Hz
keepalive that sustains pinging.

```
                         Garmin Marine Network (172.16/16)
  ┌────────────────────┐                                   ┌─────────────────────┐
  │  GCV sonar          │   multicast (we listen)           │ GPSMAP chartplotter │
  │  172.16.3.0 (GCV20) │ ───────────────────────────────► │ 172.16.6.64 (master)│
  │  .3.196 (GCV10)     │  239.254.2.1:50220  eb07 imagery  │ • HW wake/enable    │
  │                     │                     d807 markers  │ • 1 Hz keepalive    │
  │                     │  239.254.2.2:50050  8e03 status   │   (d107 → :50220)   │
  │                     │     (tx flag + depth)             │ • CDP config (owns  │
  │                     │ ◄──── 239.254.2.11:51000 ───────  │   range/freq/sched, │
  │                     │       e5/e7 08 CDP config         │   239.254.2.11:51000)│
  │   TCP :50227  ◄─────┼───────────────────────────────────────────┐           │
  └────────────────────┘   d2 07 ef be  command frames              │           │
                           (transmit / range / TVG …)               │           │
                                                          ┌─────────┴──────────┐ │
                                                          │  THIS driver (host)│ │
                                                          │ • join :50220 imagery (decode → RawSonarImage)
                                                          │ • TCP :50227 control (transmit/range)
                                                          │ • debug_raw: also capture :50050 + :51000
                                                          │ • relies on chartplotter for wake+keepalive
                                                          └────────────────────┘
```

**Streams from the sonar (multicast, listen-only):** imagery `239.254.2.1:50220`
(`eb07` data + `d807` markers, ~540 pkt/s, 3 channels), status
`239.254.2.2:50050` (`8e03`: transmit flag + depth). **Config** the chartplotter
broadcasts on `239.254.2.11:51000` (`e5/e7 08` named key/value "CDP" — owns
range/freq/schedule). **Control to the sonar:** unicast TCP `172.16.3.0:50227`
(`d2 07 ef be` frames — transmit/range/TVG; works on both generations) — the
path this driver uses, sidestepping CDP. With `debug_raw` on, the driver also
captures the `:50050` and `:51000` streams so a wet run is fully re-decodable
offline (e.g. to pin the depth-field encoding).

## Topics

Published (relative to the node namespace):

| Topic | Type | Notes |
|-------|------|-------|
| `sonar_image_port` | `marine_acoustic_msgs/RawSonarImage` | side-scan port, single beam; `DTYPE_UINT16` (GCV-20, little-endian) / `DTYPE_UINT8` (GCV-10). `rx_angles`/`tx_angles` = `0` (a fixed beam has no steering); side/orientation is carried by the per-channel `frame_id` + TF. `sample_rate` is derived from the ping's **own sub-header display range (v2)** — per channel, tracks hardware auto-range — so a consumer recovers `range = sound_speed·bins/(2·sample_rate)`; falls back to the commanded range, then `sample_rate_hz` (see `decode.derive_sample_rate`) |
| `sonar_image_starboard` | `marine_acoustic_msgs/RawSonarImage` | side-scan starboard |
| `sonar_image_down` | `marine_acoustic_msgs/RawSonarImage` | down-look (water-column) beam; its v2 is the water-column range (≠ the side-scan swath) |
| `nadir_depth` | `sensor_msgs/Range` | per-ping bottom range from the down-look sub-header **v1** (M3-validated to ~1%); beam axis = `+X` of the dedicated `<frame_id>_nadir` frame (point it down in the URDF). Uncorrected for draft/tide/offsets |
| `debug/raw` | `std_msgs/UInt8MultiArray` | raw UDP payloads — only when `debug_raw:=true`, for offline re-decode |
| `transmitting` | `std_msgs/Bool` | latched transmit state |
| `status` | `std_msgs/String` | latched one-line status |

| `state` | `marine_radar_control_msgs/RadarControlSet` | latched operator-control set (CAMP renders it) |

Subscribed:
- the sound-speed topic (default `sound_speed`, `marine_interfaces/SoundSpeed`;
  a platform launch sets the absolute path).
- `change_state` (`marine_radar_control_msgs/RadarControlValue`) — operator
  control changes (`key`/`value`), same contract as the radar driver.

Each channel publishes its own `frame_id` (`<frame_id>_port` / `_starboard` /
`_down`); the URDF/TF tree orients each transducer (side and downward tilt), so
mounting — including a non-traditional install — lives entirely in TF, never in
the driver. A proper sidescan rviz view (slant-range correction, water-column
skip, draping over terrain or a nadir-depth plane) is a separate effort.

Service: `set_transmit` (`std_srvs/SetBool`) — turn transmit on/off.

## Operator controls (radar-style)

Mirrors the `unh_marine_radar` pattern so CAMP can render the controls
dynamically: the node publishes a `RadarControlSet` on `state` and accepts
`RadarControlValue` (`key`, `value`) on `change_state`.

| Control | Type | Values | Notes |
|---------|------|--------|-------|
| `status` | enum | `standby` / `transmit` | reflects actual transmit state |
| `range` | float | `range_min_m`..`range_max_m` (m) | verified on GCV-20 |
| `tvg` | enum | `off`/`low`/`medium`/`high` | GCV-10-derived; **unverified on GCV-20** (TVG is display-side there) |
| `interference` | enum | `off`/`low`/`medium`/`high` | GCV-10-derived; **unverified on GCV-20** |

`tvg` / `interference` are gated by `expose_gcv10_controls` (default true).

## Transmit safety

- **Transmit OFF at startup** — the node asserts off and never pings without an
  explicit command (pairs with the chartplotter defaulting to not pinging on
  power-up).
- Transmit-off is also sent on node shutdown.
- No external sound-speed interlock is needed: the GCV stops pinging on its own
  when out of the water, so a dry transducer cannot overheat.

## Protocol notes

Decode is validated against a real GCV-10 survey capture (see
`test/test_decode.py`). Two fields are deliberately **not** taken from the
imagery stream because they are not reliably present there:

- **Frequency** is not encoded in the imagery sub-header, so it cannot be
  derived from the side-scan/down-look mode without baking in a transducer
  assumption. It is a per-channel parameter (`freq_*_hz`), default `0.0` =
  unavailable. Set it explicitly for a known transducer if a populated
  `ping_info.frequency` is needed.
- **Timestamp**: the stream carries no usable per-ping clock, so each scan line
  is stamped with the ROS receive time of its first packet. On an NTP-synced
  host this is accurate to a few ms; note it is receive (not transmit) time.

## Key parameters

| Parameter | Default | Notes |
|-----------|---------|-------|
| `gcv_ip` | `172.16.3.0` | GCV-20; GCV-10 = `172.16.3.196` |
| `iface_ip` | `''` | local NIC IP for the multicast join (set on multi-homed hosts) |
| `port_channels` / `stbd_channels` / `down_channels` | `[0]` / `[1]` / `[2]` | GCV-20 map; GCV-10 survey data used port=`[3]`, stbd=`[1]` |
| `freq_port_hz` / `freq_stbd_hz` / `freq_down_hz` | `0.0` | set per transducer; 0 = unavailable |
| `sample_rate_hz` | `0.0` | last-resort fallback when neither the sub-header v2 nor a commanded range gives a scale; 0 = unavailable |
| `nadir_frame_id` | `''` | frame for `nadir_depth` (+X down); empty derives `<frame_id>_nadir` |
| `nadir_beam_width_rad` | `0.0` | `Range.field_of_view` for `nadir_depth` |
| `transmit_on_startup` | `false` | safe default |
| `sound_speed` | `1500.0` | device's assumed sound speed (m/s) used to build the `sample_rate` scale and stamped into `ping_info.sound_speed`; see Protocol notes |
| `range_m` | `0.0` | >0 commands range (settable at runtime) |
| `range_min_m` / `range_max_m` | `1.0` / `60.0` | bounds of the range control |
| `expose_gcv10_controls` | `true` | include TVG / interference controls |
| `device` | `auto` | `auto` detects GCV-10 vs GCV-20 structurally from the render-layer count (picks the echo extractor); `gcv20`/`gcv10` force it |
| `debug_raw` | `false` | publish raw UDP payloads on `debug/raw` for offline re-decode; settable at runtime |

## Run

`garmin_sidescan.launch.py` is a **generic example**: no namespace, neutral
defaults. Override `gcv_ip` / `iface_ip` / `frame_id` for your host. A platform
is expected to ship its own wrapper that sets the namespace, frame prefix, and
the absolute sound-speed topic (the same split `sound_speed_bridge` uses:
`aml_svs.launch.py` here vs. the boat's `sound_speed_launch.py`); topic paths
below assume the bare example (node at `/garmin_sidescan`).

```bash
ros2 launch garmin_sidescan garmin_sidescan.launch.py \
    gcv_ip:=<GCV IP> iface_ip:=<host Marine-Network IP>

# transmit control
ros2 service call /garmin_sidescan/set_transmit \
    std_srvs/srv/SetBool "{data: true}"

# set range
ros2 param set /garmin_sidescan range_m 12.0
```

## Status / follow-ups

- Decode validated on GCV-10 real data; pending **live GCV-20 validation over a
  real bottom** (channel map {0,1,2}).
- Follow-ups (separate issues): slant-range correction, geo-referencing /
  mosaicking against nav; pure chartplotter-free operation (keepalive + wake).
