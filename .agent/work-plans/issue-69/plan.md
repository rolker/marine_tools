# Plan: kongsberg_em_bridge publishes SonarInfo (N/78 signal length et al.)

## Issue

https://github.com/rolker/marine_tools/issues/69

## Context

Follow-up to rolker/unh_marine_autonomy#240 (PR #266, merged): the
`marine_interfaces/SonarInfo` message now exists to carry the acquisition
settings needed for GeoCoder-style radiometric correction. The N/78 parser
already walks the 24-byte TX-sector blocks but skips signal length, waveform
id, and bandwidth; the existing synthetic-datagram test builder
(`test_em_datagrams.py::_build_n78`) already encodes all three
(`'<hHfffHBBf'`: tilt, focus, **siglen**, tx_delay, ctr_freq, mean_abs,
**waveform**, sector#, **bandwidth**), confirming the field offsets
(+4 f32 s, +18 uint8, +20 f32 Hz) against the .all spec the builder was
written from.

A survey test opportunity is expected the week of 2026-07-20; this is the
change that makes those bags actually contain pulse length.

## Approach

1. **`em_datagrams.py`**: extend the sector loop to also unpack
   `signal_length` (f32 s, offset +4), `waveform` (uint8, +18; Kongsberg
   0=CW, 1=FM up, 2=FM down), `bandwidth` (f32 Hz, +20). Pure decoder change,
   no ROS surface.
2. **`node.py`**:
   - New latched publisher `sonar_info` (`QoSProfile`: reliable,
     `transient_local`, depth 1) beside `detections`.
   - Pure helpers (testable without rclpy, matching the
     `test_save_rollover.py` pattern):
     - `acquisition_signature(parsed)` — the tuple of SonarInfo-relevant
       values (model, per-sector siglen/waveform/bandwidth) used for
       change detection.
     - `sonar_info_from_parsed(parsed, frame_id, stamp)` — builds the
       message: acquisition block from the datagram; intensity semantics
       for what this bridge publishes (`SonarDetections.intensities` =
       reflectivity in dB → `QUANTITY_POWER` + `INTENSITY_SCALE_DB` +
       `REFERENCE_UNCALIBRATED_RELATIVE`, `scale=1.0`/`offset=0.0`);
       correction state honest per the SonarInfo conventions block
       (`TVG_UNKNOWN`, NaN absorption/source level set explicitly,
       `ANGULAR_NORMALIZATION_UNKNOWN` — the bridge applies none but the
       sonar side is unverified); curves empty; calibration empty.
   - Publish on ping when the signature changes (stamped with the ping
     stamp), plus a heartbeat timer (`sonar_info_period` parameter,
     default 10.0 s per ADR-0009) re-publishing the last message so every
     rosbag2 split segment captures one. The heartbeat keeps the change's
     original (sonar-clock) stamp — review round-1: stamping heartbeats
     from the system clock could place them after a segment's pings when
     the clocks diverge, breaking the at-or-before association rule;
     rosbag2 assigns segments by receive time, so the stamp need not
     change. Timer callback guarded like the recv-thread publish. Shared
     state between the recv thread and the timer guarded by a lock.
   - `sonar_model` derived from the datagram model field (30 = M3 per the
     live captures the parser was validated against → `"kongsberg-m3"`;
     other values → `"kongsberg-em<model>"`).
3. **`package.xml`**: add `<depend>marine_interfaces</depend>`; description
   updated to mention SonarInfo.
4. **Tests** (`test_em_datagrams.py` + new `test_sonar_info.py`):
   - sector parse: siglen/waveform/bandwidth values round-trip through
     `_build_n78` (give the builder kwargs for them).
   - `sonar_info_from_parsed`: field population incl. NaN sentinels set,
     parallel array lengths equal, signal-type mapping (0/1/2/other).
   - `acquisition_signature`: stable across identical pings, changes when
     siglen/waveform/bandwidth/model change (drives republish-on-change).
5. **Docs**: launch file/README topic list if present.

## Out of scope

- Platform bag-record topic list (`unh_echoboats_project11`) — separate.
- Populating the ARA curve / calibration fields (consumer work, cube side).

## Verification

- `colcon build` + `colcon test` for `kongsberg_em_bridge` in the issue-69
  worktree (flake8/pep257 + unit tests).
- If an M3 capture file is available locally, `replay.py` bench check that
  `sonar_info` publishes latched and re-publishes on the heartbeat.
