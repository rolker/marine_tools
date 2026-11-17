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
frames**, and `earth` (or `--earth-frame`) must be an ancestor of the ping's
`header.frame_id` in it — a self-contained `bizzyboat_sonar` bag does; a sidescan-only
`*_sidescan_raw` bag has no nav and cannot be georeferenced. The
down-look channel is dropped (XTF is a two-channel port/starboard
format). The converted samples are raw amplitudes, untouched by any
artifact filtering.

To trim dock idle from a survey, pass `--start-time` / `--end-time`
(epoch seconds or an ISO-8601 UTC timestamp like
`2026-06-15T16:26:49`); pings outside the window are skipped. TF is read
from the start of the bag, so a ping at the start of the window has its
transforms. After `--end-time` no new ping is taken; `/tf` is read only
until the pings already held have their answer (a pose, or `tf_late` once
`--tf-wait` of bag time has passed), then the rest of the bag is not read.
A quick way to find the dock-out/dock-in times is the boat speed from
`/bizzy/odom`. Example:

```bash
ros2 run bag_analysis bag_to_xtf --bag <bag> --output survey.xtf \
    --start-time 2026-06-15T16:26:49 --end-time 2026-06-15T20:56:17
```

Other options: `--port-topic` / `--starboard-topic` / `--nadir-topic`,
`--earth-frame` (default `earth`), `--pair-tolerance` (max |Δt| to pair
port with starboard, default 0.25 s), and `--max-pings` (cap output, for
quick checks; a value below 1 is rejected). The transform options are in
the next section.

### Where each ping's pose comes from

The pose of a ping is the pose at the ping's own time, from tf2. The
transform samples just past a ping come later in the bag than the ping, so
`bag_to_xtf` holds each ping until it has read transforms on both sides of
the ping's time, then asks tf2 for the pose at exactly that time. There is
no fallback to the latest transform and no option to turn one on. (Before
this, 98% of the pings of a real bag were written with the latest
transform. Measured on bag 2026-08-25T13-59-09: 524,684 of 535,122 written
ping messages (98.05%) used the fallback, and the pose was older than the
ping stamp by 0.049 s median, 0.112 s at the 99th percentile and 0.229 s
at worst, over 267,559 port pings; the method and results are recorded in
[issue #100](https://github.com/rolker/marine_tools/issues/100). Every XTF
made earlier is affected, so regenerate any you still use.)

Which port and starboard messages form a ping is decided first, on their
stamps as recorded (`--pair-tolerance`), whatever the transforms are. The
pose is then looked up for the pair. A ping that cannot be located is
dropped **as a whole**, counted **once** under its reason, and neither of
its messages can pair with a neighbour. The drop counters count pings;
`unpaired` and `malformed` count messages.

A ping is dropped, and counted by reason in the summary, when:

| Counter | Meaning |
|---|---|
| `no_tf` | its time is before the first transform sample of a link in the chain, or a frame of the chain never appears |
| `tf_gap` | the two samples around its time, on any dynamic link of the chain, are further apart than `--max-tf-gap` |
| `tf_late` | no transform past its time arrived within `--tf-wait`, or the bag ended first |
| `tf_evicted` | its time is older than the oldest sample the `--tf-cache` still held (but not older than the first sample ever fed for that link, which is `no_tf`), so older samples had been dropped (raise `--tf-cache`; with the default and a ping waiting at most `--tf-wait` this should stay 0) |
| `tf_mismatch` | tf2 refused a lookup that the tool's own record of the samples said would work (should stay 0; if not, please report it) |

Two more counters count messages, not pings; no ping is formed from them:

| Counter | Meaning |
|---|---|
| `unpaired` | a port or starboard message whose stamp was not within `--pair-tolerance` of any message of the other channel (it was replaced by the next message of its own channel, or the bag ended first) |
| `malformed` | a message with a sample rate of 0 or less, which cannot be converted |

| Option | Default | Meaning |
|---|---|---|
| `--max-tf-gap <s>` | 1 | Largest allowed interval between the two samples around the ping time, on every dynamic link of the chain (about ten periods of a 10 Hz chain). A sample exactly at the ping time passes whatever its neighbours. Static transforms never count. tf2 itself interpolates across a gap of any length without saying so, which is why the tool checks. |
| `--tf-wait <s>` | 5 | How long a ping waits for transforms past its time, in seconds of bag time (the time the messages were recorded, not the sonar stamps). Must be larger than `--max-tf-gap`. With a negative offset (below) the pose is looked up after the stamp and the wait is extended by that much. A chain that is never published costs about this much queued data, not the whole bag. |
| `--tf-cache <s>` | 600 | tf2 cache length (at most 10,000,000). Must be larger than `--tf-wait` + `--max-tf-gap` + the offset below + `--pair-tolerance`. |
| `--stamp-to-transmit-offset <s>` | 0 | See below. |

`--max-tf-age` is gone. It limited how old a fallback "latest" transform
could be, a thing that no longer exists, and its value would now mean
something different, so passing it is an error that names `--max-tf-gap`.
Do not carry an old value over.

The run stops with exit 1 and a message, keeping the parts finished so far,
if the transform tree is one it does not support: a frame that gets a
second parent partway through the bag, or a frame published on both `/tf`
and `/tf_static`. tf2 would silently keep only one of the two, so poses
would be wrong. A loop in the tree stops the run the same way. Transforms
that tf2 itself rejects (NaN, a zero quaternion, a frame that is its own
parent, an empty frame id, data older than the cache) are not used and are
listed in the summary; so is a stamp fed twice for the same frame
(`duplicate_stamp`), which tf2 accepts and answers with whichever value
came last, so check the recording if it is not 0. A transform
that arrives after a transform with a later stamp for the same frame is
filed in order, but a ping already answered from the wider bracket is not
revisited.

#### What the sonar stamp means: `--stamp-to-transmit-offset`

The `garmin_sidescan` driver stamps each ping with the host's receive time
of its first packet, not with the time the ping was transmitted (see
"Timestamp" in `garmin_sidescan/README.md`). The ping was sent a little
earlier, and the boat moved in between. The offset says how much earlier,
in seconds: the pose is looked up at `stamp - offset`. The default is 0, so
nothing changes unless you set it. Negative values are allowed; only a
non-finite or absurd (over an hour) value is an error.
**The offset has not been measured.** This option is the place to put a
measured value; estimating one is not part of `bag_to_xtf`.

Which time is used where:

| Time | Used for |
|---|---|
| the stamp as recorded (`header.stamp`; the bag time if the stamp is zero) | `--start-time` / `--end-time`, port/starboard pairing (`--pair-tolerance`), and the ping header's ComputerClockHour/Minute/Second/Hsec fields (XTF spec: the computer clock time when the ping was received) |
| `stamp - offset` (the transmit estimate) | the pose lookup, the ping header's Year to HSeconds, the names of split parts, `--split-gap`, `--split-duration`, seconds-per-ping and speed |

With the default offset the two are the same. With an offset, a part can be
named up to the offset before the first stamp that `--start-time` admitted.
The ComputerClock fields are written on every ping, offset or not.

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

## Contacts and log entries → GeoJSON (`contacts_to_geojson`)

```bash
ros2 run bag_analysis contacts_to_geojson \
    --bag <operator bag> --log <deployment log>.md --output contacts.geojson
```

Operator contacts become points from their own `geo_pose`. Deployment-log
entries (`**<timestamp>** — text`) are placed on the boat track: the pose of
`--track-frame` (default `bizzy/base_link`) at the entry's time, from tf2,
with the same rules as `bag_to_xtf` above: transforms on both sides of the
time, no fallback to the latest transform, and `earth` must be an ancestor
of the track frame. The tool reads the whole bag's transforms first, so it
never waits; an entry after the last transform is `tf_late`. Its
`--tf-cache` (default 6 h) is the span of transforms kept: an entry older
than what the cache still holds at the end of the bag, but not older than the
first transform of the recording, is `tf_evicted` (older than the recording
is `no_tf`), and
the tool says so and names the option to raise. Rejected or re-fed
transforms are listed after the summary line. Log timestamps have minute precision at best, so an entry is
located only as well as its time is known.

An entry that cannot be located is still written, with `"geometry": null`,
and a `geo_status` property: `exact` (located), `no_tf`, `tf_gap`,
`tf_late`, `tf_evicted` or `tf_mismatch`, with the meanings of the table
above. The old
`approx`, `stale` and `missing` statuses are gone.

`--max-tf-gap` (default 1 s, the same bar as `bag_to_xtf`) replaces
`--max-tf-age`, which is now an error that names it. Raise it for a
transform chain slower than about 1 Hz. The same unsupported trees as above
(a re-parented frame, a frame on both `/tf` and `/tf_static`) stop the run
with exit 1.

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
