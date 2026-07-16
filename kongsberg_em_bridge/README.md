# kongsberg_em_bridge

Bridge a Kongsberg `.all` UDP stream from an M3 multibeam to ROS 2. Decodes
the Raw Range and Angle 78 (`N`) datagram — the one the M3 populates (its
XYZ88 is exported empty) — and publishes one detection set per ping. Purely a
wire-format translator: geometry and TPU happen downstream
(`cube_bathymetry/detections_to_pointcloud`).

## Topics

| Topic | Type | QoS | Content |
|---|---|---|---|
| `detections` | `marine_acoustic_msgs/SonarDetections` | sensor data (best effort) | Per-ping two-way travel times, tx/rx angles, per-beam reflectivity (dB) as `intensities`, validity flags. |
| `sonar_info` | `marine_interfaces/SonarInfo` | reliable, `transient_local`, depth 1 | Latched acquisition metadata (ADR-0009): pulse length, bandwidth, signal type per TX sector; intensity semantics of `detections.intensities`; honest-unknown correction state. Re-published on acquisition change (stamped with the ping) and on a slow heartbeat so every rosbag2 split segment captures one. |

## Services

| Service | Type | Purpose |
|---|---|---|
| `~/set_recording` | `std_srvs/SetBool` | Start/stop raw `.all` recording at runtime. |

## Parameters

| Parameter | Default | Purpose |
|---|---|---|
| `bind_address` / `bind_port` | `0.0.0.0` / `20002` | UDP socket for the M3's exported `.all` stream. |
| `frame_id` | `m3` | Frame for both published topics. |
| `skip_invalid_beams` | `true` | Drop beams the sonar flagged invalid (required by the CUBE error model). |
| `sonar_info_period` | `10.0` | SonarInfo heartbeat seconds; must be shorter than the recorder's shortest split segment. `<= 0` disables (not recommended when recording). |
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
