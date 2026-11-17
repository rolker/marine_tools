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
format). The converted samples are raw amplitudes, untouched by any
artifact filtering.

To trim dock idle from a survey, pass `--start-time` / `--end-time`
(epoch seconds or an ISO-8601 UTC timestamp like
`2026-06-15T16:26:49`); pings outside the window are skipped (TF is
still consumed across the whole bag, so georeferencing is unaffected).
A quick way to find the dock-out/dock-in times is the boat speed from
`/bizzy/odom`. Example:

```bash
ros2 run bag_analysis bag_to_xtf --bag <bag> --output survey.xtf \
    --start-time 2026-06-15T16:26:49 --end-time 2026-06-15T20:56:17
```

Other options: `--port-topic` / `--starboard-topic` / `--nadir-topic`,
`--earth-frame` (default `earth`), `--pair-tolerance` (max |Δt| to pair
port with starboard, default 0.25 s), `--max-tf-age` (drop pings whose
only TF is older than this, default 1 s), and `--max-pings` (cap output,
for quick checks; a value below 1 is rejected).

### Splitting the output

Big bags make big files (0.1 to 3.5 GB each for the Appledore exports),
and some receiving tools fail above 2 GB. So `bag_to_xtf` splits its
output into parts by default:

| Option | Default | Meaning |
|---|---|---|
| `--split-size <MB>` | 500 | Start a new part before it would grow past this size. 1 MB is 10^6 bytes, so a part never exceeds the limit (except a single ping that is larger by itself, which gets a part alone). `0` turns the size limit off. |
| `--split-duration <minutes>` | off | Start a new part when the current one covers this many minutes of pings. |
| `--split-gap <seconds>` | 60 | Start a new part when more than this many seconds pass between two pings, for example when the sonar was switched off. `0` turns it off. |
| `--no-split` | | One file, whatever its size, as before. Cannot be combined with the three options above. |
| `--overwrite` | off | Let the run replace existing files of its output (see "Existing files" below). Without it the run refuses to start if any exist. |

Negative, non-finite (`inf`, `nan`) and absurdly large values are
rejected, as is a duration or gap that rounds to 0 ns. A size limit over 2 GB or smaller than one
ping prints a warning. Whichever limit is reached first starts the next
part, and the report says which one it was.

Naming: a run that ends with one part writes the plain `--output` name,
so existing scripts keep working. A run with several parts writes
`<stem>_<UTC time of the part's first ping><suffix>`, for example
`survey_20260615T150345Z.xtf`, and no plain-name file. Two parts that
start in the same second get `_2`, `_3` and so on.

Ping numbers, speed and the seconds-per-ping field run on across parts,
so the pings of all parts joined together (everything after each file's
1024-byte header) are byte for byte the pings of the unsplit file. One
consequence: the first ping after a time gap carries a speed averaged
over the gap (distance over the silent interval), just as in the
unsplit file. Treat it as unreliable.

The run report lists each part with its ping count, first and last ping
time (UTC) and size.

Each part is written to a `.partial` file, synced to disk and renamed
when it is complete. If the run fails part way, the finished parts stay,
only the unfinished `.partial` is deleted (the report says how many
pings were in it), the finished parts are listed, and the exit status is
non-zero.

#### Existing files: refused unless `--overwrite`

Before the bag is read, `bag_to_xtf` looks in the output folder for the
plain `--output` file and for any file of this output's part-name
pattern (the time-stamped parts, their `_2` variants and `.partial`
leftovers). If there is any and `--overwrite` was not given, it prints
the files (a long list is cut, with the count), writes nothing and exits
with status 2. This also covers a `--max-pings` test run over a full
export, and it is a change from the single-file behaviour before
splitting: a rerun used to replace the plain output silently, now it
refuses. `--output` that is a directory, or a folder that cannot be
written, is refused up front the same way (status 2).

With `--overwrite` the run replaces the files whose names it produces
and the report lists every file it replaced. An old `.partial` is
unlinked and created afresh, never opened for writing, so a symlink or a
hard link under that name is removed as a name and its target (or the
other link) is left alone; a finished part replaces an existing name by
rename, which also swaps the name without writing through it. A
one-part run goes through its time name before it takes the plain name,
so an old file of that time name is replaced (and listed) too. Old files of the pattern that
the run did not regenerate (for example the extra parts of an earlier,
larger split) are never deleted; the run ends with a warning that the
folder now mixes two runs and lists them. Remove them yourself when they
are out of date.

Without `--overwrite` a file that appears while the run is going is not
replaced, with one caveat that depends on the filesystem. `.partial`
files are always created exclusively. A finished part is then moved to
its name by the first of these that the filesystem supports:

1. a hard link followed by removing the `.partial`: atomic, fails if the
   name exists (the normal case on local disks and most NAS shares);
2. where hard links are not supported (FAT, some SMB shares), an atomic
   no-replace rename (Linux `renameat2` with `RENAME_NOREPLACE`): the
   same guarantee;
3. where neither exists, a check that the name is free followed by a
   rename. This is the only path with a gap: a file created in the
   microseconds between the check and the rename would be replaced. The
   up-front refusal has already ruled out every name that existed when
   the run started.

If the name is taken, the run fails with a non-zero status and the file
that appeared is untouched. The finished part is complete data: it stays
under its `.partial` name, and the report names it so you can rename it
yourself. Parts already finished are listed as before. Syncing a part to
disk is skipped on filesystems that cannot do it (`EINVAL`, `ENOSYS`,
`ENOTSUP`); an I/O error while syncing still fails the run.

If listing the folder or printing the summary fails after a complete
conversion, the run still exits 0: the parts are written. It says so and
says that the check for files of earlier runs could not be done.

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
