# Plan: bag_to_xtf: look each ping's pose up at the ping's own time, not the latest transform

## Issue

https://github.com/rolker/marine_tools/issues/100

## Context

`_iter_pings` looks the pose up the moment a ping is read. The /tf samples
past the ping's stamp are later in the bag, so 98.3% of pings on the
2026-08-25 bag fall back to the latest transform (stale by 0.05 s median,
0.23 s worst). Owner direction (Roland, final): use tf2 as designed (hold
each ping until transforms past its lookup time have been read, then a
stamped lookup), no latest-transform fallback and no escape hatch. The
Garmin stamp is a host receive time (`garmin_sidescan/README.md`,
"Timestamp"), so an explicit stamp-to-transmit offset (default 0) is added;
estimating it is out of scope.

`contacts_to_geojson` imports `_lookup_pose` and has the same fallback, so it
is fixed here too, through a shared module. It reads the whole bag's /tf into
the Buffer first and does its lookups afterwards, so it never waits; it only
needs the same bracket rule and the same "no later sample means no answer".

## Revision after the plan review (2026-11-17)

Every must-fix and suggestion of the Plan Review is folded in below; the
owner's decisions replace the open questions. Verified against the installed
Jazzy `tf2_ros` with `scratchpad/xtf_split/issue100/tf2_probe*.py`:

| tf2 behaviour (installed, Jazzy) | Consequence |
|---|---|
| Python Buffer interpolates across a gap of any length (60 s tested); a lookup past the newest or before the oldest sample raises `ExtrapolationException` (single sample: only the exact stamp works) | The tool must track bracketing stamps itself to bound gaps; "ready" means newest >= L >= oldest on every dynamic link |
| Leading `/` is stripped from both frame ids | Tracker strips it too |
| `set_transform` returns nothing and only logs a rejection (NaN, zero quaternion, self transform, empty ids, data older than newest - cache); non-unit quaternions are accepted | The tracker applies those rules itself and feeds the Buffer only what it accepted, so both hold the same samples |
| Insert is rejected when `stamp + cache < newest` (strict; exactly at the cutoff is kept) | Same rule in the tracker |
| Pruning of old samples uses the newest stamp from before the insert (a one-insert lag) | Tracker prunes the same way |
| A duplicate stamp is accepted but adds no new stamp | Stamp set, so deduplicated |
| Re-parenting: bracketing samples with different parents are not interpolated (the earlier sample is returned) | Fatal error, not supported |
| A frame in both /tf and /tf_static: the cache is replaced by whichever arrived last | Fatal error, not supported |
| Quaternion sign flip between samples is slerped correctly | Truth in tests must do the same |

## Approach

1. **Shared lookup module** `bag_analysis/tf_lookup.py` (both tools use it;
   unit-tested on its own). `TfChain(earth_frame, cache_s, max_gap_s)` owns
   the tf2 `Buffer` and a tracker of **accepted sample stamps only** (sorted
   list per dynamic child, `bisect.insort` only when out of order, trimmed by
   moving a start index and compacting, never `pop(0)` in a loop), the child
   -> parent map and the set of static children. Frames are fed only through
   `TfChain.feed(transforms, static)`, which applies tf2's rejection rules
   first and passes only accepted transforms to the Buffer. It raises
   `TfChainError` (clear message, naming the frame and both parents) when a
   child gets a second parent, or is seen in both /tf and /tf_static, or the
   chain has a loop. `check(frame, lookup_ns)` walks the chain from `frame` up
   to the earth frame and judges every dynamic link (static links exempt):
   - `no_tf`: L is before the link's oldest stamp (and L <= 0);
   - `tf_gap`: the samples bracketing L are further apart than
     `--max-tf-gap`; an exact sample at L passes whatever its neighbours
     are; first/last samples are the same rule (no bracket = not ready, no
     bracket possible = `no_tf`);
   - `wait_chain`: a frame or link not seen yet; `wait_late`: every link is
     known but a link's newest stamp is before L;
   - `ready` otherwise.
   Definitive failures on links already known win over a wait on another
   link. `settle(verdict)` turns a wait into a final answer when nothing more
   will arrive: `wait_chain` -> `no_tf`, `wait_late` -> `tf_late`.
   `lookup(frame, lookup_ns)` is the Buffer's stamped lookup; if it raises
   after a `ready` verdict it raises `TfMismatch` (tracker and tf2 disagree),
   which the tools count separately as `tf_mismatch`. Decisions use the
   tool's own stamps, never exception text.
2. **Lookup time.** L = stamp - offset in integer ns. `--stamp-to-transmit-offset
   SECONDS` default 0, negative values allowed; a non-finite value, or one
   over an hour either way (a typo), is a usage error (exit 2). L <= 0 is `no_tf` (tf2 reads time 0 as "latest").
3. **Which clock is used where** (decided by the owner; checked, no concrete
   reason found against it):
   - raw stamp (`header.stamp`, or the bag record time when the stamp is
     zero): `--start-time`/`--end-time`, port/starboard pairing
     (`--pair-tolerance`). These select recorded data.
   - ping time = raw stamp - offset (the transmit estimate; the instant the
     pose is looked up at): the XTF ping header Year..HSeconds, the part
     names, `--split-gap`, `--split-duration`, seconds-per-ping and speed.
   - The raw stamp goes into the ping header's ComputerClockHour (236),
     Minute (237), Second (238), Hsec (239), UTC, written on every ping
     including offset 0 (so the file means the same whatever the option;
     the four bytes are new, so the golden file changes by exactly those).
   Offset is one constant for both channels, so pairing differences and
   inter-ping intervals are identical on either clock; only labels move. A
   part can therefore be named up to |offset| before the first raw stamp that
   `--start-time` admitted; documented.
   Times are made from the integer nanoseconds (`_utc_from_ns`), truncated to
   microseconds, then to hundredths by the writer: the earlier float path
   rounded an instant such as x.009999999 or x.999999999 up over the
   hundredth or the second (found by the hand-worked boundary tests of
   review round 1).
4. **Pairing first, then the hold queue in `_iter_pings`.** (Revised after
   local review round 1: pairing used to follow the lookup, so a ping dropped
   for a transform reason left its mate pending, and the mate paired with the
   next transmit's opposite channel, 0.1 s later and inside the tolerance.
   Reproduced by `test_a_ping_with_one_unresolvable_half_is_dropped_whole`
   before the fix.) A message that passes the window and the `sample_rate`
   check is paired at once with the waiting message of the other channel on
   the **stamps as recorded, and nothing else** (`_pair_by_stamp`); which two
   messages form a ping no longer depends on any transform. A pair is
   appended to a FIFO queue (`_HeldPing`: both messages, the record time and
   altitude snapshot of the completing, later-read message). After every
   message the head is resolved repeatedly: both halves `ready` -> a stamped
   lookup for each (a `TfMismatch` on either is counted `tf_mismatch`, never a
   fallback) and `yield`; a definite refusal of either half (`no_tf`,
   `tf_gap`, `tf_evicted`) -> the whole ping is dropped and counted once; a
   wait on either -> stop (head-of-line blocks, so emitted order is as read).
   Nothing is left pending by a dropped ping, so it cannot pair with a
   neighbour and is not also counted `unpaired`. A message replaced in
   `pending` by a later one of the same channel, or left at the end, is
   `unpaired`. `_lookup_pose` and the `approx` status are deleted.
   **What the counters count:** `no_tf`, `tf_gap`, `tf_late`,
   `tf_evicted` and `tf_mismatch` count **pings** (a port and a starboard
   message paired on their stamps, once each); `paired` counts pings;
   `port msgs`/`starboard msgs`, `malformed`, `unpaired` and `out_of_window`
   count **messages**. The report prints them on two lines that say so.
5. **Bounded hold, on bag record time.** The wait limit is `--tf-wait`
   (default 5 s), measured from the ping's own record time against the record
   time of the message just read (record time is monotone; stamps are not
   used, so a bad stamp on any topic cannot flush the queue). A negative
   offset looks the pose up `|offset|` after the stamp, so the wait is
   `--tf-wait + max(0, -offset)`: otherwise every ping would be `tf_late`
   for a legitimate offset, whatever the cause looked like (review round 1).
   It is its own quantity: it must exceed
   `--max-tf-gap` (usage error otherwise) and covers the 0.05 to 0.23 s
   bag-order lag seen on the real bag plus a missing-sample allowance. When
   it expires the ping is dropped as `settle(verdict)`. At the end of the bag
   the remaining pings are settled the same way (`tf_late` after the last
   sample). Usage error unless `--tf-cache` > `--tf-wait` + `--max-tf-gap` +
   |offset|, so a held ping cannot outlive the Buffer cache. A chain that is
   never published costs about `--tf-wait` of queued pings, not the bag.
6. **Options.** `--max-tf-gap` (default 1.0 s, about 10x the 10 Hz period):
   the largest allowed interval between the two transform samples that
   bracket the lookup time, on every dynamic link of the chain. Passing the old
   `--max-tf-age` is a usage error (exit 2) whose message names
   `--max-tf-gap` and says the meaning changed (done with a hidden option
   that errors, so `--max-tf-a` prefix matching cannot hit it either:
   `allow_abbrev=False`). New: `--tf-wait`, `--stamp-to-transmit-offset`.
   Report: first line unchanged (`wrote N XTF pings ...`), then the
   two counter lines (`dropped pings ...: no_tf= tf_gap= tf_late=
   tf_mismatch=` and `dropped messages ...: malformed= unpaired=`), then a line with the offset and the pose time (`pose looked up
   at stamp - offset; ping time written = that`) and any transforms the
   tracker rejected. `approx_tf` and its note are removed.
7. **`--end-time`**: the first ping past the end time is still counted in
   `counts[side]` (as today) but stops admission without `break`. After that
   point every message of any subscribed topic only advances the clock: /tf
   is still fed, nothing else is counted, and the queue is resolved (so held
   pings settle on the wait limit by record time, with no more /tf needed).
   When the queue is empty the generator returns without reading the rest of
   the bag (review round 1; before, a bag whose /tf stopped was read to the
   end).
8. **`--max-pings`**: unchanged consumer `break`/`close()`; the finally block
   discards the queue without counting it as `unpaired` or dropped (only the
   half-pair in `pending` counts, as today).
9. **Failure**: `TfChainError` ends the conversion with a clear error and
   exit 1; finished parts are kept and the unfinished `.partial` removed, as
   for any other failure.
10. **`contacts_to_geojson`**: after reading all /tf and /tf_static it asks
    `TfChain.check(track_frame, L)` and settles waits (`wait_chain` ->
    `no_tf`, `wait_late` -> `tf_late`). `--max-tf-age` is replaced by
    `--max-tf-gap`, default 1.0 s: the same bar as the sonar tool, a log
    entry is a point on a track, a bracket of ten sample periods on a 10 Hz
    chain is already generous, and it drops only a real outage; a slower
    chain is handled by raising it, deliberately and visibly. (The old 5 s
    meant "age of the latest sample", a different quantity.) Passing
    `--max-tf-age` is the same usage error. `geo_status` values become
    `exact` (stamped lookup done), `no_tf`, `tf_gap`, `tf_late`,
    `tf_mismatch`; `approx`/`stale`/`missing` are gone, and the summary line
    is updated. The log entries have minute precision; that is the entry's
    own uncertainty and is documented, not changed. A `TfChainError` stops
    the run with exit 1.
11. **Writer**: `XtfWriter.write_ping` takes an optional `computer_time`
    datetime and writes it at offsets 236..239 (spec rev 42, text copy at
    `.agent/scratchpad/xtf_split/xtf_spec/xtf_spec_rev42.txt`, checked against
    `writer.py`'s other offsets: Year 14, Hour 18, HSeconds 21 are the ping
    time; 236..239 are the computer clock).
12. **Tests**, written first for the moving track and shown failing on the
    old code (result recorded in `progress.md`):
    - `xtf_fakes.py`: a moving, turning track (heading rate >= 10 deg/s,
      speed several m/s) sampled at 10 Hz with the pings at fractional
      positions between samples (0.3, 0.7), a quaternion sign flip, bag order
      with /tf after the pings, more than one ping held at once, one
      out-of-order /tf stamp; an expected-pose helper that computes ECEF
      truth from the two bracketing samples with real slerp, independent of
      tf2.
    - `test_xtf_pose_time.py`: written lat/lon/heading equal the truth within
      field precision; before first sample, after last, inside a gap longer
      and shorter than `--max-tf-gap` (neighbours unaffected); two-link
      chain with different rates and a gap in the slow link; held ping at end
      of bag; `--end-time` keeps the last pings before it; `--max-pings`
      leaves counts clean; chain never published is dropped `no_tf` and the
      queue stays bounded; wait expiry on record time not on stamps (a ping
      with a far-future stamp does not flush others); offset: lookup moves
      exactly, written time and ComputerClock fields, part names, window and
      pairing follow decision 3, negative and zero accepted, non-finite
      rejected; `--max-tf-age` rejected naming `--max-tf-gap`;
      `--tf-wait`/`--tf-cache` validation; re-parent and dual /tf+/tf_static
      end the run with exit 1 and a message; a lookup that fails after
      `ready` counts `tf_mismatch` (forced with a stub).
    - `test_tf_lookup.py`: the tracker rules against the installed Buffer,
      including a seeded differential test (random insert order, duplicates,
      old data, pruning; tracker `ready`/`tf_gap` <=> Buffer lookup ok when
      the gap limit is infinite), exact-sample case, leading slash, rejected
      transforms, mismatch.
    - Split: with tf-after-pings, parts joined equal the unsplit output byte
      for byte (offset 0 and non-zero).
    - `test_xtf_cli.py`: the four `_lookup_pose` tests are replaced; its
      docstring that mentions the fallback is fixed.
    - `test_contacts_to_geojson.py`: the exporter with a fake bag (moving
      track): interpolated location, before first, after last, gap, wait
      statuses, `--max-tf-age` rejected, error on re-parent.
    - Golden: run `test_xtf_golden.py` before touching it. The four
      ComputerClock bytes per ping are new, so it is expected to change; it
      is regenerated only if the bytes differ, and the commit message and
      the test docstring say why. A separate assertion that every byte
      outside offsets 236..239 of each ping header is unchanged keeps the
      reason honest.
13. **Real-data check** (read only on the bag; output only under
    `.agent/scratchpad/xtf_split/issue100/`): run the fixed tool on bag
    2026-08-25T13-59-09+00-00 with split and `--no-split`, read the XTF back
    and compare per-ping heading and position with the pose interpolated at
    the ping's time from the bag's /tf (independent code path), counters by
    reason, byte equality of the joined parts with the unsplit file, time and
    peak memory against the old code, and runs with offset +0.05 and a
    negative offset. Large outputs deleted, `RESULT.txt` kept.
14. **Docs**: `bag_analysis/README.md` (the option line, the new options,
    what the stamp means for the Garmin sidescan with a pointer to
    `garmin_sidescan/README.md` "Timestamp", offset not measured, which clock
    each thing uses, pings wait for /tf past their time, fatal tf errors,
    contacts changes, an upgrade note: every XTF made before is biased), the
    `bag_to_xtf.py` and `contacts_to_geojson.py` module docstrings.

## Files to Change

| File | Change |
|------|--------|
| `bag_analysis/bag_analysis/tf_lookup.py` | new: shared chain tracker, bracket check, stamped lookup |
| `bag_analysis/bag_analysis/cli/bag_to_xtf.py` | hold queue, new options, written time vs raw stamp, remove `_lookup_pose`/`approx_tf`, report, `--end-time`, validation, `TfChainError` exit |
| `bag_analysis/bag_analysis/cli/contacts_to_geojson.py` | use the shared module; `--max-tf-gap`; statuses |
| `bag_analysis/bag_analysis/xtf/writer.py` | ComputerClock fields |
| `bag_analysis/test/test_tf_lookup.py`, `test_xtf_pose_time.py` | new |
| `bag_analysis/test/test_xtf_cli.py`, `test_xtf_split.py`, `test_xtf_writer.py`, `test_contacts_to_geojson.py`, `xtf_fakes.py` | replace lookup tests, join test, writer test, moving-track fake, exporter tests |
| `bag_analysis/test/data/xtf_golden_single.xtf`, `test_xtf_golden.py` | regenerate (ComputerClock bytes) |
| `bag_analysis/README.md` | as item 14 |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Test what breaks | Every drop path, the order/pairing guarantees and the old-code failure are tested. |
| Only what's needed | Offset is one subtraction; no estimation, no per-sample motion, no escape hatch. |
| A change includes its consequences | README, help text, report, golden, the `approx` tests and docstrings are in the plan. |
| Human control and transparency | Drops are counted by cause; the offset used is printed. |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| 0008 ROS 2 conventions | Yes | tf2 used as designed (wait, then stamped lookup); REP-103 frames untouched. |
| 0009 Python packages | No | No new dependencies. |
| 0013 progress.md | Yes | Plan Authored entry appended. |

## Consequences

| If we change... | Also update... | Included? |
|---|---|---|
| `--max-tf-age` replaced by `--max-tf-gap` | help, README, any script passing it (explicit usage error names the new option) | Yes |
| Written poses and XTF times (with an offset) | every XTF produced earlier is biased; mention in PR | Yes (PR text, README) |
| `approx_tf` removal | report, tests, `tf_staleness.py` mimic | Yes (scratch script only) |
| Hold queue | altitude snapshot, `--end-time`, `--max-pings`, split join | Yes |
| `_lookup_pose` removal | `contacts_to_geojson` and its statuses and summary | Yes |
| ComputerClock bytes | golden file | Yes |

## Documentation & Instruction Impact

- **Stale docs** (must land in this PR): `bag_analysis/README.md`,
  `bag_to_xtf.py` and `contacts_to_geojson.py` docstrings and option help,
  `test_xtf_cli.py` docstring.
- **Agent-instruction candidates** (proposals only): the marine_tools repo
  has no `.agents/`; candidate for the workspace knowledge: tf2's Python
  Buffer interpolates across a gap of any length and exposes no bracketing
  stamps, so a tool that must bound gaps tracks stamps itself; and its
  `set_transform` reports a rejected insert only in the log.

## Open Questions

All three earlier questions are decided by the owner (see Approach 2, 3, 6).
Left open and not decided here: the actual receive-to-transmit offset of the
Garmin sidescan (to be measured separately), and the driver's stamping
(separate, design-first work).

## Estimated Scope

Single PR, closes #100.
