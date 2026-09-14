# kongsberg_em_bridge

Bridge a Kongsberg `.all` UDP stream from an M3 multibeam to ROS 2. Decodes
the Raw Range and Angle 78 (`N`) datagram — the one the M3 populates (its
XYZ88 is exported empty) — and publishes one detection set per ping. Purely a
wire-format translator: geometry and TPU happen downstream
(`cube_bathymetry/detections_to_pointcloud`).

## Topics

| Topic | Type | QoS | Content |
|---|---|---|---|
| `detections` | `marine_acoustic_msgs/SonarDetections` | sensor data (best effort) | Per-ping two-way travel times, tx/rx angles, per-beam reflectivity (dB) as `intensities`, validity flags. Every beam the sonar reported is published — see [Invalid beams](#invalid-beams). |
| `sonar_info` | `marine_interfaces/SonarInfo` | reliable, `transient_local`, depth 1 | Latched acquisition metadata (ADR-0009): pulse length, bandwidth, signal type per TX sector; intensity semantics of `detections.intensities`; honest-unknown correction state. Re-published on acquisition change (stamped with the ping) and on a slow heartbeat so every rosbag2 split segment captures one. |

### Invalid beams

Every beam the sonar reported is published, carrying its honest
`DetectionFlag`: `DETECT_OK`, or `DETECT_BAD_SONAR` for a beam the sonar
flagged invalid. There is no parameter to filter them — the driver reports
what the sensor reported, and deciding what to do with a flagged beam is the
consumer's job. Dropping them here destroyed the fact both live and in the
bag (the data of record), and left the flag field decorative, since every
published beam was then `DETECT_OK`.

**The consumer is not fixed yet.** `cube_bathymetry`'s error model does not
consult `DetectionFlag`
([rolker/cube_bathymetry#154](https://github.com/rolker/cube_bathymetry/issues/154)),
so it will read an invalid beam's zero two-way travel time as a sounding at
zero depth — seafloor at the surface. That applies to **any CUBE ingest path,
live or offline**: the same loop runs in the live node and in the offline
importers (`import_bag`, `batch_regen`, `bag_to_geotiff`), so a bag recorded
after this change and re-imported before `#154` lands writes those zero-depth
soundings into the persistent store, not just a transient cloud. That is a bug
in the consumer, tracked and fixed there rather than worked around here; the
node says so in a warning at startup, so it is visible in the log beside the
data, and the warning is retired with `#154`. Until then, treat M3 data
ingested by the CUBE error model with that in mind.

All per-beam arrays (`flags`, `two_way_travel_times`, `tx_delays`,
`intensities`, `tx_angles`, `rx_angles`, and the beamwidth arrays when
populated) are built in one loop and are always the same length — one element
per beam the sonar reported. A test pins that invariant.

## Sensor constants

### Beamwidths

The M3's `.all` stream carries no beamwidth of its own — reading a raw capture,
the only datagram types present are attitude, surface sound speed, raw range
and angle 78, XYZ88 and clock. There is no runtime-parameters datagram, which
is where a Kongsberg system would state its beamwidths. So `ping_info`'s
beamwidths can only come from a small device table in `node.py`
(`_RX_BEAMWIDTH_RAD` / `_TX_BEAMWIDTH_RAD`, resolved by
`_resolve_beamwidths`), keyed by the `.all` model number — the same key
`sonar_model_name` already uses.

Values are full −3 dB widths in **radians**, per `PingInfo.msg`; **not**
half-angles, and not degrees. `rx_beamwidths` is the across-track (receive)
width, `tx_beamwidths` the along-track (transmit) width. Both are per
*published* beam: they are appended in the same loop as `two_way_travel_times`
and the other per-beam arrays, so all of them are always the same length —
`cube_bathymetry` indexes the beamwidth arrays with the same per-beam index.

| Model | Sonar | rx (across-track) | tx (along-track) |
|---|---|---|---|
| 30 | M3 | not populated | not populated |
| (any other) | — | not populated | not populated |

**The M3 is uncharacterised.** No sourced beamwidth figure for it exists — not
in a datasheet on hand, not anywhere in this workspace — so the fields are left
**empty** rather than stamped with a guess. That is deliberate, and it is the
same convention `garmin_sidescan` uses for its GCV-10 generation: a wrong
beamwidth stamped confidently is worse than an absent one, because absence is
handled explicitly by the consumer and a wrong number silently is not. An empty
field makes the CUBE error model fall back to its own generic `Device`
beamwidth; the fields are never zero-filled.

Populating them in radians is *safe* today: `cube_bathymetry#144` (fixed by
[rolker/cube_bathymetry#153](https://github.com/rolker/cube_bathymetry/pull/153))
made the consumer normalize units once at the `Device` boundary and validate
each per-beam value before trusting it, retiring the historical hazard where a
correctly-populated radians value was consumed as degrees. What is missing is a
cited figure, not a safe consumer — so filling in the table is a follow-up
whenever a datasheet figure and its conditions turn up (marine_tools#85).

## Services

| Service | Type | Purpose |
|---|---|---|
| `~/set_recording` | `std_srvs/SetBool` | Start/stop raw `.all` recording at runtime. |

## Parameters

| Parameter | Default | Purpose |
|---|---|---|
| `bind_address` / `bind_port` | `0.0.0.0` / `20002` | UDP socket for the M3's exported `.all` stream. |
| `frame_id` | `m3` | Frame for both published topics. |
| `sonar_info_period` | `10.0` | SonarInfo heartbeat seconds; must be shorter than the recorder's shortest split segment. `<= 0` disables (not recommended when recording). |
| `angular_response_curve_file` | `''` | Empirical angular-response curve CSV (from `cube_bathymetry`'s `derive_angular_response.py`) declared in SonarInfo with its TL provenance; empty = no curve. Loaded once at startup. |
| `save_all_dir` | `''` | Directory for raw `.all` recording (genuine Kongsberg framing, loadable by Caris/Qimera/MB-System). Empty disables. |
| `save_all_max_seconds` / `save_all_max_bytes` | `0` / `0` | Optional `.all` segment rollover triggers; `0` disables each. |
| `record_on_start` | `false` | Record `.all` from startup (otherwise arm via `~/set_recording`). |

## Tools

- `launch/kongsberg_em_bridge.launch.py` — node launch with the common
  arguments exposed.
- `replay.py` (module `kongsberg_em_bridge.replay`) — replay a captured
  datagram stream (the big-endian length-framed `m3_udp_capture.py` format —
  deliberately distinct from saved `.all` files) over UDP for bench testing.

See `.agent/work-plans/issue-1/` and marine_tools#69 for design history;
ADR-0009 (in `unh_marine_autonomy/docs/decisions/`) for the SonarInfo
contract.
