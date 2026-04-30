# Plan: Add `bag_analysis` package: extract → parquet → report pipeline for deployment bags

## Issue

https://github.com/rolker/marine_tools/issues/5

## Context

Post-deployment bag analysis has been ad-hoc; each new question needs a
fresh re-read of the bag. Architecture agreed in design discussion:
two-stage CLI pipeline (`bag_to_parquet`, `parquet_to_report`),
ament_python package inside this repo alongside `sound_speed_bridge`,
PNGs+`summary.md` committed under
`unh_echoboats_project11/docs/logs/<year>/<deployment>/<bag-name>/`,
parquet sidecars stay local. Validated against the 2026-04-29 cod rock
survey bag (251 MB, 42 topics, ~2.18 hr).

## Approach

1. **Scaffold ament_python package** — `package.xml`, `setup.py`,
   `setup.cfg`, `resource/bag_analysis`, `README.md`, mirroring
   `sound_speed_bridge` conventions (ADR-0008). Every Python source
   file gets the BSD-3-Clause copyright header used elsewhere in
   the repo.
2. **Reader layer** (`bag_analysis/reader.py`) — `rosbag2_py` sequential
   reader, deserialize via `rclpy.serialization`. Iterate (topic, msg,
   t_ns) tuples.
3. **Extractor registry** (`bag_analysis/extractors/`) — per-message-type
   functions `extract(msg) -> dict[str, Any]`. Flatten ROS messages to
   flat columns; dispatch by `msg_type` string. Tier 1 needs extractors
   for: `mavros_msgs/State`, `sensor_msgs/BatteryState`,
   `mavros_msgs/RCOut`, `sensor_msgs/NavSatFix`, `sbg_driver/SbgGpsPos`,
   `sbg_driver/SbgGpsVel`, `sbg_driver/SbgEkfNav`, `sbg_driver/SbgGpsHdt`,
   `sbg_driver/SbgStatus`, `nav_msgs/Odometry`,
   `geometry_msgs/TwistStamped`, `marine_interfaces/Heartbeat`,
   `nav2_msgs/BehaviorTreeLog`, `diagnostic_msgs/DiagnosticArray`,
   `udp_bridge_interfaces/BridgeInfo`,
   `udp_bridge_interfaces/TopicStatisticsArray`. Unknown types fall
   through to `{json: <repr>}` (skip rather than crash).
4. **Parquet writer** (`bag_analysis/parquet_writer.py`) — one parquet
   file per topic via `pyarrow.parquet`, schema inferred per-topic from
   first N messages. Sanitize topic name → filename
   (`_bizzy_mavros_battery.parquet`). Write `_topic_index.json` (topic
   → file, msg type, count) and `_bag_meta.json` (start_ns, duration_ns,
   source_path).
5. **Parquet reader helpers** (`bag_analysis/parquet_reader.py`) —
   `load_topic(parquet_dir, topic) -> pd.DataFrame` keyed off the
   index. Used by all plot generators.
6. **Plot generators** (`bag_analysis/plots/`) — one module per Tier 1
   plot. Each exposes `generate(parquet_dir, output_dir) -> PlotResult`
   returning PNG path + summary stats.
7. **Report orchestrator** (`bag_analysis/report.py`) — runs each Tier 1
   plot, writes `summary.md` with the bag header (start time, duration,
   topic count, message count) followed by per-plot sections (PNG
   embed + the summary stats from `PlotResult`).
8. **Two CLI entry points** (`bag_analysis/cli/`) — `bag_to_parquet`
   and `parquet_to_report`, both registered as console_scripts. Argparse
   with `--bag`, `--parquet-dir`, `--output`, `--topics` (whitelist),
   `--tier` (default 1), `--robot-namespace` (default `bizzy`). Plot
   modules use a small helper `topic(name)` that prefixes
   robot-scoped names with `/<namespace>/` and passes through
   system topics (`/diagnostics`, `/tf`, `/tf_static`, `/rosout`,
   `/marine/platforms`) unchanged. The system-topic allowlist lives
   in one place (`bag_analysis/topics.py`) — plot modules don't
   make per-topic decisions.
9. **Tests** (`test/`) — unit tests using boundary-mocked inputs:
   construct ROS message instances directly in-process and call the
   extractor; build synthetic DataFrames and call the plot generator.
   No real bag fixture — the contracts under test are extractor
   flatten and plot consumption, both reachable without a writer +
   schema-registration round trip. Per "Test what breaks": cover at
   least one extractor (battery — typed numerics) and one plot
   (mode timeline — string-step rendering).
10. **README** — purpose, install, two CLI usage examples, how to add
    a new extractor, how to add a new plot, parquet schema description.

## Tier 1 plot inventory → topic mapping

| Plot | Primary topics | Notes |
|---|---|---|
| Mode timeline | `/bizzy/mavros/state`, `/bizzy/marine/status/mission_manager`, `/bizzy/behavior_tree_log` | Step plot of mode strings vs time |
| Track | `/bizzy/sensors/sbg/gps_pos`, `/bizzy/mavros/global_position/raw/fix` | Lat/lon plot, color by `gps_pos.status` (RTK fix grade) |
| Speed/heading vs COG | `/bizzy/odom`, `/bizzy/sensors/sbg/ekf_nav`, `/bizzy/sensors/sbg/gps_vel`, `/bizzy/sensors/sbg/gps_hdt` | Heading-vs-COG drift indicator |
| Power | `/bizzy/mavros/battery`, `/bizzy/mavros/rc/out` | Voltage, current, per-channel PWM, derived watts |
| Comms | `/bizzy/udp_bridge/bridge_info`, `/bizzy/udp_bridge/topic_statistics`, `/bizzy/udp_bridge/remotes/operator/*` | Bandwidth, drops over time |
| Sensor health | `/diagnostics`, plus per-topic rate from bag metadata | NaN counts where applicable, dropouts |
| Altitude/heave | `/bizzy/sensors/sbg/ekf_nav` (altitude field) | Launch/recovery detection from altitude transitions |

## Files to Change

| File | Change |
|------|--------|
| `bag_analysis/package.xml` | New — `<depend>` on rclpy, rosbag2_py, rosidl_runtime_py, and message pkgs (mavros_msgs, sbg_driver, marine_interfaces, sensor_msgs, nav_msgs, geometry_msgs, diagnostic_msgs, tf2_msgs, udp_bridge_interfaces, std_msgs, visualization_msgs, nav2_msgs); `<exec_depend>` on the pure-Python runtime libraries (python3-pandas, python3-pyarrow, python3-matplotlib) |
| `bag_analysis/bag_analysis/topics.py` | New — system-topic allowlist + `topic(name, namespace)` helper used by all plot modules |
| `bag_analysis/setup.py` | New — entry_points for `bag_to_parquet` and `parquet_to_report` |
| `bag_analysis/setup.cfg` | New — flake8/pep257 config matching `sound_speed_bridge` |
| `bag_analysis/resource/bag_analysis` | New — empty marker |
| `bag_analysis/bag_analysis/{__init__,reader,parquet_writer,parquet_reader,report}.py` | New — core modules |
| `bag_analysis/bag_analysis/extractors/*.py` | New — per-message-type extractors |
| `bag_analysis/bag_analysis/plots/*.py` | New — per-plot generators |
| `bag_analysis/bag_analysis/cli/{bag_to_parquet,parquet_to_report}.py` | New — CLI entry points |
| `bag_analysis/test/test_*.py` + fixtures | New — unit tests |
| `bag_analysis/README.md` | New — usage + extension docs |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Only what's needed | Tier 1 only; explicit "out of scope" for Tier 2/3, IzzyBoat adapters, field-host automation, GitHub Actions |
| Test what breaks | Tests target extractor flattening + plot generation contract, not rosbag2 internals |
| A change includes its consequences | README + tests in same PR; usage examples invoke the CLIs directly (`ros2 run bag_analysis ...`) — no workspace coupling |
| Improve incrementally | Single PR; first iteration validated against one real bag; extensibility designed in but not all extractors landed |
| Workspace vs. project separation | Package lives entirely in `marine_tools` (cross-boat); zero workspace involvement — CLIs invoked via `ros2 run` directly |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| 0008 — ROS 2 conventions | Yes (new package) | Mirror `sound_speed_bridge` layout: ament_python build_type, BSD-3-Clause, format-3 package.xml, conventional setup.py with console_scripts |
| 0009 — Python package management | Yes (Python deps) | All deps declared in `package.xml` (`<depend>` for build+runtime, `<exec_depend>` for pure-Python runtime libs) and resolved by rosdep — pandas/pyarrow/matplotlib all have rosdep keys; no pip, no .venv |
| 0002 — Worktree isolation | Yes | Worktree created at `layers/worktrees/issue-marine_tools-5/` before any edits |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| `extractors/` API | All plot modules importing extractor outputs | Yes — kept internal; not a public ROS API |
| Parquet schema (`_topic_index.json`) | Any external readers of the parquet sidecars | First iteration: docs only. Schema versioning deferred until there is a non-`bag_analysis` reader |
| New extractor | `bag_analysis/README.md` "how to add" section | Yes — README is part of this PR |

## Open Questions

(none — the three planning questions resolved before implementation:
parquet sidecars go to `~/data/bag_reports/<bag-name>/parquet/`,
committed `report/` lands under `unh_echoboats_project11/docs/logs/<year>/<deployment>/<bag-name>/`;
`--robot-namespace` CLI flag with default `bizzy`; no workspace
coupling — CLIs are invoked directly via `ros2 run`.)

## Estimated Scope

Single PR in `rolker/marine_tools` for the package itself (~1500–2500
LOC including tests + README). First-validation deliverable: a
generated `report/` for the cod rock bag, posted as a comment on this
issue or on the deployment issue.
