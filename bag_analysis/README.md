# bag_analysis

Two-stage post-deployment bag analysis for marine ROS 2 deployments.

```
.mcap bag → SQLite extract → markdown report + PNG plots
```

The first stage (`bag_to_sqlite`) reads a rosbag2 once and writes one
table per topic into a single SQLite file. The second stage
(`sqlite_to_report`) loads those tables and renders the Tier-1 report
(7 plots + a summary) without ever re-reading the bag. Ad-hoc questions
(e.g. peak current during line 3) are answerable directly via the
`sqlite3` CLI on the same DB file.

## Tier-1 plot inventory

| Plot | What it shows | Topics |
|---|---|---|
| Mode timeline | mavros mode + autonomy state vs time | `mavros/state`, `marine/status/mission_manager`, `behavior_tree_log` |
| Track | Lat/lon path, colored by GNSS status | `mavros/global_position/raw/fix` (primary), `sensors/sbg/gps_pos` (fallback) |
| Speed/heading vs COG | Drift indicator | `odom`, `mavros/global_position/raw/gps_vel` (primary); `sensors/sbg/gps_vel`, `sensors/sbg/gps_hdt` (supplementary) |
| Power | Battery voltage + PWM channels | `mavros/battery`, `mavros/rc/out` |
| Comms | UDP bridge throughput + drops (Mbps) | `udp_bridge/bridge_info`, `udp_bridge/topic_statistics` |
| Sensor health | Per-topic message rates + diagnostic levels | `/diagnostics`, bag metadata |
| Altitude/heave | Altitude vs time (launch/recovery, tide proxy) | `mavros/global_position/raw/fix` (primary), `sensors/sbg/ekf_nav` (fallback) |

## Usage

```bash
# Extract a bag to SQLite (one-time per bag)
ros2 run bag_analysis bag_to_sqlite \
    --bag /path/to/2026-04-29T21-43-29+00-00 \
    --output ~/data/bag_reports/2026-04-29T21-43-29+00-00/data.db

# Render the Tier-1 report from the SQLite (cheap; rerun freely)
ros2 run bag_analysis sqlite_to_report \
    --db ~/data/bag_reports/2026-04-29T21-43-29+00-00/data.db \
    --output ~/data/bag_reports/2026-04-29T21-43-29+00-00/report
```

Both CLIs accept `--robot-namespace` (default `bizzy`) so the same code
runs against IzzyBoat or any other namespace once topics align.

### Ad-hoc queries

The extract is a regular SQLite database, so the `sqlite3` CLI gives you
quick answers without touching Python:

```bash
sqlite3 ~/data/bag_reports/.../data.db \
    "SELECT MAX(voltage), MIN(voltage) FROM t_bizzy_mavros_battery"

sqlite3 .../data.db ".tables"            # list per-topic tables
sqlite3 .../data.db "SELECT topic, count FROM _topic_index ORDER BY count DESC LIMIT 10"
```

## Output layout

```
<output>/data.db                 # SQLite extract — _bag_meta + _topic_index + one t_<topic> table per topic
<output>/report/
├── summary.md                   # bag header + per-plot sections
├── mode_timeline.png
├── track.png
└── ...
```

The `report/` directory is what gets committed under
`unh_echoboats_project11/docs/logs/<year>/<deployment>/<bag-name>/`
alongside the deployment log. `data.db` stays on the analyst's machine —
regenerate it from the bag if you need it again.

## Extending

### Add a message extractor

1. Create `bag_analysis/extractors/<short_name>.py` with an
   `extract(msg) -> dict[str, Any]` returning a flat-keyed dict of
   columns to land in SQLite.
2. Register the extractor in `bag_analysis/extractors/__init__.py`
   keyed by the canonical message-type string (e.g.
   `'sensor_msgs/msg/Imu'`).

Unknown types fall through to a JSON-string fallback — no need to add
extractors for topics you don't actually plot. Topics whose message
type isn't installed on the ROS path are skipped with a warning rather
than crashing the pipeline.

### Add a plot

1. Create `bag_analysis/plots/<plot_name>.py` exposing
   `generate(db_path, output_dir, namespace) -> PlotResult`.
2. Register the plot in `bag_analysis/plots/__init__.py` along with
   the tier it belongs to.

`PlotResult` carries the PNG path plus a list of summary stats that
land in `summary.md`.

## Sidescan → XTF export (`bag_to_xtf`)

`bag_to_xtf` converts recorded Garmin sidescan into an
[XTF](https://en.wikipedia.org/wiki/EXtended_Triton_Format) file that
standard survey tools read (OpenSideScan and MB-System on Linux;
SonarWiz/Triton on Windows).

```bash
ros2 run bag_analysis bag_to_xtf \
    --bag /path/to/bizzyboat_sonar/2026-06-15T15-03-45+00-00 \
    --output ~/data/sidescan/2026-06-15.xtf
```

It reads the port and starboard `marine_acoustic_msgs/RawSonarImage`
channels, pairs them per ping, and writes a two-channel (port/starboard)
XTF. Each ping is georeferenced by composing the bag's TF tree offline
and looking up `earth → <ping frame_id>`; since `earth` is the ECEF
frame, that yields the transducer's true lat/lon plus heading/pitch/roll
relative to local north. Slant range comes from the ping geometry
(`(sample0 + n_samples) · sound_speed / (2 · sample_rate)`), so the
near-field gate (`sample0`) is included. Altitude-above-bottom is taken
from the latest `nadir_depth` (`sensor_msgs/Range`).

**The bag must contain the full TF chain from `earth` to the sidescan
frames** — a self-contained `bizzyboat_sonar` bag does; a sidescan-only
`*_sidescan_raw` bag has no nav and cannot be georeferenced. The
down-look channel is dropped (XTF is a two-channel port/starboard
format). Key options: `--port-topic` / `--starboard-topic` /
`--nadir-topic`, `--earth-frame` (default `earth`), `--pair-tolerance`
(max |Δt| to pair port with starboard, default 0.25 s), and
`--max-pings` (cap output, for quick checks). The converted samples are
raw amplitudes, untouched by any artifact filtering.

## Schema

`data.db` holds:

- One table per topic, named `t_<sanitized_topic>` (e.g.
  `/bizzy/mavros/battery` → `t_bizzy_mavros_battery`). Every table has
  a `t_ns` column (int64 ns since epoch, indexed) plus columns for the
  flattened message fields.
- `_bag_meta` (key, value) — `start_ns`, `duration_ns`,
  `source_bag_path`, `total_messages`. Values stored as JSON strings.
- `_topic_index` (topic, table_name, msg_type, count) — the inventory
  the report stage uses to find tables.

Schema versioning is deferred until something outside `bag_analysis`
needs to read these files — for now, the contract is "regenerate from
the bag if the schema changes."

## Tests

Tests live under `test/` and use boundary mocks rather than a real bag
fixture: they construct ROS message instances directly for the
extractor tests and write small synthetic SQLite databases for the plot
tests. Run via the standard ROS workflow:

```bash
cd <workspace>/layers/main/sensors_ws
colcon build --packages-select bag_analysis --symlink-install
colcon test --packages-select bag_analysis
colcon test-result --verbose
```
