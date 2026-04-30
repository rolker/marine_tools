# bag_analysis

Two-stage post-deployment bag analysis for marine ROS 2 deployments.

```
.mcap bag → parquet sidecars → markdown report + PNG plots
```

The first stage (`bag_to_parquet`) reads a rosbag2 once and writes one
parquet file per topic. The second stage (`parquet_to_report`) loads
those parquet files and renders the Tier-1 report (7 plots + a summary
table) without ever re-reading the bag.

## Tier-1 plot inventory

| Plot | What it shows | Topics |
|---|---|---|
| Mode timeline | mavros mode + autonomy state vs time | `mavros/state`, `marine/status/mission_manager`, `behavior_tree_log` |
| Track | Lat/lon path, colored by GNSS fix grade | `sensors/sbg/gps_pos`, `mavros/global_position/raw/fix` |
| Speed/heading vs COG | Drift indicator | `odom`, `sensors/sbg/ekf_nav`, `sensors/sbg/gps_vel`, `sensors/sbg/gps_hdt` |
| Power | Battery V/I, PWM channels, derived watts | `mavros/battery`, `mavros/rc/out` |
| Comms | UDP bridge throughput + drops | `udp_bridge/...` |
| Sensor health | Per-topic message rates + diagnostic levels | `/diagnostics`, bag metadata |
| Altitude/heave | Altitude vs time (launch/recovery, tide proxy) | `sensors/sbg/ekf_nav` |

## Usage

```bash
# Extract a bag to parquet (one-time per bag)
ros2 run bag_analysis bag_to_parquet \
    --bag /path/to/2026-04-29T21-43-29+00-00 \
    --output ~/data/bag_reports/2026-04-29T21-43-29+00-00/parquet

# Render the Tier-1 report from parquet (cheap; rerun freely)
ros2 run bag_analysis parquet_to_report \
    --parquet-dir ~/data/bag_reports/2026-04-29T21-43-29+00-00/parquet \
    --output ~/data/bag_reports/2026-04-29T21-43-29+00-00/report
```

Both CLIs accept `--robot-namespace` (default `bizzy`) so the same code
runs against IzzyBoat or any other namespace once topics align.

## Output layout

```
<output>/parquet/
├── _bag_meta.json            # start_ns, duration_ns, source path
├── _topic_index.json         # topic → file, msg type, count
├── _bizzy_mavros_battery.parquet
├── _bizzy_odom.parquet
└── ...

<output>/report/
├── summary.md                # bag header + per-plot sections
├── mode_timeline.png
├── track.png
└── ...
```

The `report/` directory is what gets committed under
`unh_echoboats_project11/docs/logs/<year>/<deployment>/<bag-name>/`
alongside the deployment log. The `parquet/` directory stays on the
analyst's machine — regenerate it from the bag if you need it again.

## Extending

### Add a message extractor

1. Create `bag_analysis/extractors/<short_name>.py` with an `extract(msg)
   -> dict[str, Any]` function that returns a flat-keyed dict of the
   columns you want in parquet.
2. Register the extractor in `bag_analysis/extractors/__init__.py` keyed
   by the canonical message-type string (e.g. `'sensor_msgs/msg/Imu'`).

Unknown types fall through to a JSON-string fallback — no need to add
extractors for topics you don't actually plot.

### Add a plot

1. Create `bag_analysis/plots/<plot_name>.py` exposing
   `generate(parquet_dir, output_dir, namespace) -> PlotResult`.
2. Register the plot in `bag_analysis/plots/__init__.py` along with the
   tier it belongs to.

`PlotResult` carries the PNG path plus a list of summary stats that
land in `summary.md`.

## Schema

Parquet files are one-per-topic. Filenames are the topic name with `/`
replaced by `_` (so `/bizzy/mavros/battery` → `_bizzy_mavros_battery.parquet`).
Schemas are inferred per-topic from the first batch of messages. Every
file has a `t_ns` column (int64 nanoseconds since epoch) plus the
flattened message fields.

`_topic_index.json` maps `<topic> -> {file, msg_type, count}`.
`_bag_meta.json` carries `start_ns`, `duration_ns`, `source_bag_path`,
and `total_messages`.

Schema versioning is deferred until something outside `bag_analysis`
needs to read these files — for now, the contract is "regenerate from
the bag if the schema changes."
