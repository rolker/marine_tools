# Plan: bag_to_xtf: look each ping's pose up at the ping's own time, not the latest transform

## Issue

https://github.com/rolker/marine_tools/issues/100

## Context

`_iter_pings` looks the pose up the moment a ping is read. The /tf samples
past the ping's stamp are later in the bag, so 98.3% of pings on the
2026-08-25 bag fall back to the latest transform (stale by 0.05 s median,
0.23 s worst). Owner direction: use tf2 as designed (wait for samples past
the ping, look up at that time), no latest-transform fallback and no escape
hatch. The Garmin stamp is a receive time, so an explicit
stamp-to-transmit offset (default 0) is added; estimating it is out of scope.

## Approach

1. **Chain tracker** (new `bag_analysis/xtf/tf_chain.py`, unit-tested on its
   own). tf2's Python Buffer interpolates across a gap of any length and
   exposes no bracketing stamps, so the tool tracks, from the same /tf and
   /tf_static messages it feeds the Buffer: child -> parent, the static
   frames, and a sorted stamp list per dynamic child (insort, since bag order
   is not stamp order; trimmed past `--tf-cache`). `check(frame, L_ns)`
   walks the chain to the earth frame and answers, for every dynamic link
   (static links exempt, mixed rates handled because each link is checked):
   - `wait`: a frame or link not seen yet, or its newest stamp is before L;
   - `no_tf`: its oldest stamp is after L (before the first sample);
   - `stale_tf`: the samples bracketing L are further apart than
     `--max-tf-age`;
   - `ready` otherwise.
   The tool's own newest/oldest stamps decide this, never exception text.
2. **Lookup time.** L = ping stamp - offset (integer ns). A `--stamp-to-transmit-offset`
   option, seconds, default 0, must be finite and >= 0 (a negative value is
   a usage error, exit 2): the ping was transmitted `offset` seconds before
   its stamp. It applies only to the lookup. Window gating
   (`--start/--end-time`), port/starboard pairing and split decisions stay on
   the raw stamp. The report prints the value.
3. **Hold queue in `_iter_pings`.** A ping that passes the window and the
   `sample_rate` check is appended to a FIFO queue with its altitude
   snapshotted at read time (the nadir value current when the pair completes,
   exactly as today, so altitude does not change in any file). After every
   message the head is resolved repeatedly: `ready` -> `Buffer.lookup_transform`
   at L (a TransformException here is counted `no_tf`, never a fallback),
   then the existing pairing code and `yield`; `no_tf`/`stale_tf` -> count and
   skip; `wait` -> stop, head-of-line blocks, so emitted order, pairing and
   `unpaired` are as today. `_lookup_pose` and the `approx` status are
   deleted.
4. **Bounded hold.** A clock tracks the newest stamp read from /tf and the
   sonar topics. A held ping whose L is more than `--max-tf-age` behind that
   clock is released as dropped: `stale_tf` if its chain has a sample before
   L (the samples stopped or the gap exceeds the limit), else `no_tf`. At end
   of bag, remaining pings resolve with no more waiting (`no_tf`, after the
   last sample). A chain that is never published therefore costs about one
   `max-tf-age` of queued pings, not the whole bag. Usage error (exit 2) unless
   `--tf-cache` > `--max-tf-age` + offset, so a held ping cannot outlive the
   Buffer cache.
5. **Redefine `--max-tf-age`** (default 1.0 s, about 10x the 10 Hz period):
   the largest allowed interval between the transform samples bracketing the
   ping time. Update the help text and README. Remove the `approx_tf` count
   and its report note. Report gains `stamp-to-transmit offset: X s (pose
   looked up at stamp - offset)`.
6. **`--end-time`**: the first ping past the end time stops admission but
   does not `break`; /tf keeps being read until the queue is empty (or the bag
   ends), then the generator returns. Messages past the end are not counted,
   as now.
7. **`--max-pings`**: unchanged consumer `break`/`close()`; the finally block
   discards the queue without counting it as `unpaired`/dropped (only the
   half-pair in `pending` counts, as today).
8. **XTF ping time**: see Open Questions, item 1. Default recommendation is
   the raw stamp.
9. **Tests** (extend `xtf_fakes.py` with a moving, turning track sampled at
   10 Hz with pings off the sample grid, `tf_after_pings` bag ordering, and
   an expected-pose helper that lerps/slerps the two bracketing samples
   independently of tf2):
   - `test_xtf_pose_time.py`: written lat/lon/heading equal the
     interpolated truth with /tf after the pings in bag order (also run once
     against the old code to confirm it fails); ping before first sample,
     after last sample, inside a gap longer than `--max-tf-age` and one
     shorter (each counted correctly, neighbours unaffected); a two-link chain
     with different rates and one gap in the slow link; held ping at end of
     bag; `--end-time` keeps the last pings before it; `--max-pings` leaves
     counts clean; chain never published is dropped `no_tf` without growing
     the queue; offset: lookup moves back by exactly the offset, written time
     and pairing stay on the raw stamp, negative offset rejected, report prints
     it; `tf_cache` check.
   - Split test: with tf-after-pings, parts joined equal the unsplit output
     byte for byte (reuse the join helper in `test_xtf_split.py`).
   - Replace the four `_lookup_pose` tests in `test_xtf_cli.py` with
     chain-tracker unit tests; fix its docstring that mentions the fallback.
   - Golden: run `test_xtf_golden.py` first. The fake sends /tf with the same
     stamp as each ping, so the exact sample exists and I expect the bytes
     not to change; if they do, regenerate `xtf_golden_single.xtf` and state
     why (poses now at the ping's own time) in the commit and test docstring.
10. **Real-data check** (read only on the bag; output only under
    `.agent/scratchpad/xtf_split/`): run the fixed tool on bag
    2026-08-25T13-59-09+00-00, then a script equivalent to
    `tf_staleness.py` reads the XTF back and compares each written
    heading/position with the pose from a Buffer interpolated at the ping's
    own time (within the XTF field precision), and confirms the report has
    no fallback line and that dropped pings equal those outside the bracket
    limits. Also reproduce the old 0.10 deg median offset against the
    previous output as sanity, and run once with a non-zero offset on a short
    slice to confirm the pose moves and the XTF time does not.
11. **Docs**: `bag_analysis/README.md` (line 144 `--max-tf-age`, the new
    option, what the stamp means for the Garmin sidescan with a link to
    `garmin_sidescan/README.md` "Timestamp", offset not yet measured, pings
    wait for /tf past their time), and the `bag_to_xtf.py` module docstring.

## Files to Change

| File | Change |
|------|--------|
| `bag_analysis/bag_analysis/xtf/tf_chain.py` | new: chain tracker and bracket check |
| `bag_analysis/bag_analysis/cli/bag_to_xtf.py` | hold queue, offset option, `--max-tf-age` meaning, remove `_lookup_pose`/`approx_tf`, report, `--end-time`, validation |
| `bag_analysis/test/test_xtf_pose_time.py` | new tests above |
| `bag_analysis/test/test_xtf_cli.py`, `test_xtf_split.py`, `xtf_fakes.py` | replace lookup tests, join test, moving-track fake |
| `bag_analysis/test/data/xtf_golden_single.xtf`, `test_xtf_golden.py` | only if bytes change |
| `bag_analysis/README.md` | as item 11 |

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
| `--max-tf-age` meaning | help, README, any script passing it | Yes |
| Written poses | every XTF produced earlier is biased; mention in PR | Yes (PR text) |
| `approx_tf` removal | report, tests, `tf_staleness.py` mimic | Yes (scratch script only) |
| Hold queue | altitude snapshot, `--end-time`, `--max-pings`, split join | Yes |

## Documentation & Instruction Impact

- **Stale docs** (must land in this PR): `bag_analysis/README.md` (line 144
  and new offset/stamp text), `bag_to_xtf.py` docstring and `--max-tf-age`
  help, `test_xtf_cli.py` docstring.
- **Agent-instruction candidates** (proposals only): the marine_tools repo
  has no `.agents/`; candidate for the workspace knowledge: tf2's Python
  Buffer interpolates across a gap of any length and exposes no bracketing
  stamps, so a tool that must bound gaps tracks stamps itself.

## Open Questions

1. **Which time goes in the XTF ping header when the offset is non-zero?**
   Identical when the offset is 0.
   - A, raw stamp: header time is what the bag, the driver and the window
     options say; part names, split gaps and seconds-per-ping stay consistent
     with `--start/--end-time`; changing the offset changes only the
     position, not the file's times. Cost: time and position then refer to
     instants `offset` apart (a tool that looks the time up in other nav
     data would be off by the offset).
   - B, stamp - offset: the header is self-consistent (the estimated
     transmit time and the pose belong together). Cost: it bakes an
     unmeasured estimate into every time in the file; part names and
     window gating (raw) disagree with header times by the offset; changing
     the offset renames parts and shifts all times.
   Recommendation: A. The offset is an estimate and belongs in the
   report, not in the data's clock.
2. Negative offsets are rejected (a receive stamp cannot be before
   transmit). Veto if a negative value is wanted.
3. A ping whose chain stops mid-bag is reported `stale_tf` (after the hold
   limit), while after-last-sample at bag end is `no_tf`. Acceptable?

## Estimated Scope

Single PR, closes #100.
