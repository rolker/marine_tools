---
issue: 100
---

# Issue #100 — bag_to_xtf: look each ping's pose up at the ping's own time, not the latest transform

## Issue Review
**Status**: complete
**When**: 2026-11-17 12:00 -05:00
**By**: Claude Code Agent (Claude Sonnet)

**Issue**: #100
**Comment**: (best-effort post follows this entry; not recorded inline)
**Scope verdict**: well-scoped

### Actions
- [ ] Hold-queue design: resolve pings strictly FIFO from a queue so emitted order, port/starboard pairing and `unpaired` counts stay as today; head-of-line drops must not reorder.
- [ ] Snapshot `latest_altitude` when the ping is read (not when it is released), or altitude silently changes in every file; decide and state it in the golden regeneration reason.
- [ ] `--end-time`: do not `break` straight away; stop admitting pings but keep reading /tf until held pings resolve or the bag ends, otherwise the last pings before the end time are dropped as no_tf.
- [ ] `--max-pings`: consumer stop closes the generator; the held queue is discarded and must not count as `unpaired`/dropped. Test it.
- [ ] Bound the hold queue (memory): if /tf stops or the frame is never published, pings pile up until bag end. Drop a held ping as `no_tf` once messages stamped more than a limit past it have been read (tie to `--max-tf-age`), and count it.
- [ ] Bracket/gap limit: tf2's Python Buffer interpolates across a gap of any length inside the cache and exposes no bracketing stamps. Track per dynamic child frame the stamps of the chain (previous/next sample around the lookup time) in the tool and drop when the bracketing interval exceeds the limit. Chains mix rates, so check every dynamic frame in the earth->sonar chain, not just one. Static transforms are exempt.
- [ ] Redefine `--max-tf-age` as the max allowed interval (s) between the transform samples bracketing the ping time (default 1.0 s is fine, ~10x the 10 Hz period); update help text and README line 144. Pre-first / post-last sample stays `no_tf`; gap exceeded stays `stale_tf`. Remove `approx_tf` count and the report note.
- [ ] Wait condition: a lookup failing for "needs a later sample" must be told apart from "before first sample"; use own tracked newest/oldest stamps, not exception text.
- [ ] `--tf-cache` (600 s) must exceed any hold; confirm held pings cannot outlive the cache.
- [ ] Regenerate `bag_analysis/test/data/xtf_golden_single.xtf`; the fake in `xtf_fakes.py` sends /tf at the same stamps as pings so the file will probably change; state the reason (poses now at ping time) in the commit and test docstring. Update test_xtf_cli.py docstring that mentions the fallback.
- [ ] New tests per acceptance: moving+turning track with tf after pings in bag order (fails on old lookup), before first / after last / inside a gap, split parts joining to the unsplit output, held ping at end of bag.
- [ ] Offset option: include it (see recommendation below). Pin the convention (lookup time = stamp - offset, offset >= 0 means transmit earlier), apply to lookup only, keep the written ping time, pairing and window gating on the raw stamp, print the value in the report.
- [ ] README (bag_analysis): what the stamp means for the Garmin sidescan (receive time, link to garmin_sidescan README "Timestamp"), offset unmeasured.

### Recommendations
- Offset option belongs here: it is one subtraction with default 0, it is the only place the receive-vs-transmit finding can be acted on without another code change, and it shares the lookup-time change and the held-ping wait. Keep it minimal: no estimation, no per-sample motion. Risk is only a sign-convention mistake; one test pins it. Do not apply it to the XTF time field.
- Do not add a latest-transform escape hatch for any reason.

## Plan Authored
**Status**: complete
**When**: 2026-11-17 12:57 -05:00
**By**: Claude Code Agent (Claude Sonnet)

**Plan**: `.agent/work-plans/issue-100/plan.md` at `39dccb9`
**Branch**: feature/issue-100 at `39dccb9`
**Phases**: single

### Open questions
- [ ] XTF ping-header time when offset is non-zero: raw stamp (recommended) or stamp - offset
- [ ] Negative offsets rejected (usage error) - confirm
- [ ] Chain that stops mid-bag reports stale_tf after the hold limit; end-of-bag after-last reports no_tf - confirm

## Plan Review
**Status**: complete
**When**: 2026-11-17 13:00 -05:00
**By**: Claude Sonnet

**Plan**: `.agent/work-plans/issue-100/plan.md` at `39dccb9`
**PR**: PR-less
**Verdict**: changes-requested

### Findings
- [ ] (must-fix) Deleting `_lookup_pose` breaks `contacts_to_geojson.py` (`from .bag_to_xtf import _lookup_pose, _stamp_ns`, line 50; used line 166). It is not in Files to Change or Consequences, and that tool has its own `--max-tf-age` (default 5.0) with the old fallback meaning. Keep the function for it, move it, or port that tool; decide and list it — `plan.md:114-119,144`
- [ ] (must-fix) Hold bound clock is wrong: it includes sonar stamps, and L = stamp - offset, so a ping is already (offset) behind the clock the moment it is read. With offset >= `--max-tf-age` every ping is dropped at once; with offset just under it a legitimately late transform is dropped. The validation only requires tf-cache > max-tf-age + offset, not offset < bound. Also one bad/future stamp on any topic flushes the whole queue. Use bag record time (`t_ns`, monotone) as the clock, measured from the ping's own record time, and give the hold its own quantity rather than `--max-tf-age` — `plan.md:49-57`
- [ ] (must-fix) Hold bound and gap bound are conflated. `--max-tf-age` is the largest legal sample spacing; a ping whose next sample is legitimately up to that far after L (plus any bag-order lag, which the issue shows is 0.05-0.23 s typical) is dropped at exactly max-tf-age, so a ping the bracket check would have accepted is lost. The wait must exceed max-tf-age plus a lag allowance (a separate, generous default, e.g. several seconds, still bounded memory) — `plan.md:49-57`
- [ ] (must-fix) The tracker does not mirror tf2 as stated. Verified in geometry2 `cache.cpp`/`buffer_core.cpp` (humble tree on this host; check jazzy): (a) parent is per sample, not per child; bracketing samples with different parents are not interpolated (the earlier sample is returned), so a single child->parent map can be wrong across a re-parent; (b) a frame seen in both /tf and /tf_static has its cache REPLACED by whichever arrived last (static cache vs time cache), wiping earlier samples; (c) leading '/' is stripped from frame ids; (d) data older than newest - cache_time is rejected on insert and only exact duplicates are dropped; (e) the Python Buffer does not report a rejected insert, so the tracker can hold samples tf2 does not. Either (preferred, simpler) track only stamps per child frame, treat a child that ever shows a second parent or both static and dynamic as a loud error (stop with a clear message) instead of supporting it, and update the tracker only for transforms the Buffer accepted by the same rules; or mirror all of (a)-(e) with tests. Also count a lookup failure after a `ready` verdict in its own counter (tracker/tf2 disagreement), not as `no_tf` — `plan.md:19-31,43-46`
- [ ] (must-fix) `--max-tf-age` changes meaning under the same name (previously the max age of a fallback "latest" TF, no longer exists) and the sibling tool keeps the old meaning under the same flag. A script passing a large value (e.g. 60) to tolerate stale TF would now silently georeference across a 60 s interpolated gap, the silent failure this issue exists to remove; a small value would drop most pings. Use a new name (e.g. `--max-tf-gap`) and make `--max-tf-age` on bag_to_xtf an explicit usage error pointing at it — `plan.md:58-62`
- [ ] (suggestion) Bracket check: define the exact-sample case (a sample at L passes whatever the neighbours' spacing), and pre-first/post-last as the same rule, so the golden's exact-stamp samples cannot be flagged `stale_tf` — `plan.md:25-30`
- [ ] (suggestion) Altitude snapshot: state which ping's snapshot the pair uses (the completing, later-read one, to match today) — `plan.md:41-43`
- [ ] (suggestion) `--end-time`: today the first past-end ping is counted in `counts[side]` before the break; say whether the new code keeps that; keep reading only until the queue is empty, not to end of bag — `plan.md:63-66`
- [ ] (suggestion) Moving-track test: make the heading rate and speed large enough that one sample interval of staleness exceeds XTF field precision (e.g. >=10 deg/s), place pings at fractional positions (0.3, 0.7) so nearest-sample code also fails, include a quaternion sign flip, hold more than one ping at once, and one out-of-order /tf stamp case; compute truth in ECEF from the samples with real slerp, independent of tf2 — `plan.md:72-86`
- [ ] (suggestion) The "run once against old code" check is a manual mutation check; record its result in progress.md, since it will not be in the suite — `plan.md:76-77`
- [ ] (suggestion) Report: keep the line starting `wrote N XTF pings` first (golden test uses startswith); put the offset line after — `plan.md:60-62`
- [ ] (suggestion) Memory: per-frame stamp list bounded by tf-cache is fine (e.g. 100 Hz x 600 s = 60k ints per frame); use append with insort only on out-of-order and trim by slice, not pop(0) in a loop — `plan.md:22-24`

### Answers
1. Tracker: not correct as designed for re-parenting, dual /tf+/tf_static frames, or rejected inserts (see must-fix 4); correct for out-of-order stamps. "Ready" guarantees the stamped lookup only if the tracker's stamp set equals tf2's, hence the stamps-only, fail-loud-on-surprise simplification. Memory fine. No simpler way that keeps tf2 as the interpolator and still bounds gaps.
2. Yes, late transforms can be dropped, via the clock and conflation defects above; `--max-tf-age` is the wrong quantity for the wait.
3. Verified: the fake emits /tf at the same stamp as each ping, before it, so an exact sample exists; with the rules fixed as in the suggestion above (ready when newest >= L, exact sample passes), the golden bytes and `startswith` report assertions should not change. Pairing and altitude give the same values for the drop_starboard case.
4. Mostly; with the test hardening above it fails on the old lookup and has an independent truth. The independent slerp truth is the right call.
5. Sound apart from the count detail; FIFO release keeps order, pairing and unpaired identical; close() discarding the queue is right.
6. Not safe; new name (must-fix 5).

### Owner choices (view only)
- XTF header time with non-zero offset: raw stamp (A).
- Negative offsets: reject for now (a receive stamp cannot precede transmit); revisit if a trigger-stamped sensor needs it.

## Implementation
**Status**: complete
**When**: 2026-11-17 13:33 -05:00
**By**: Claude Sonnet

**Plan**: `.agent/work-plans/issue-100/plan.md` (amended in place for the owner decisions and the Plan Review)
**Branch**: feature/issue-100 (local only, not pushed, no PR)

### Commits
- `1c65b78` plan folded; `c2fe5d6` moving-track test first; `614efa5` shared `tf_lookup.TfChain`; `501f4a4` writer ComputerClock fields; `f25e42c` contacts_to_geojson; `22fa965` bag_to_xtf hold queue + options + offset + golden; `ce79cfe` tests; `6c49fd9` README; `dbe3ced` import order; `91f891a` plan note.

### Failing on the old code (c2fe5d6, before any fix)
The moving-track test failed: 59 of 60 pings written, every one through the latest-transform fallback, heading off by a median 4.05 deg (max 4.95 deg), position by up to 1.3 m from the pose interpolated at the ping stamp (truth from own slerp, track turning 15 deg/s at 4 m/s, quaternion sign flips, pings at 0.3/0.7 between samples, /tf after the pings in bag order).

### Verified against installed Jazzy tf2 (scratchpad xtf_split/issue100/tf2_probe*.py, pinned in test_tf_lookup.py incl. a seeded differential test)
Gap of any length is interpolated; lookups past newest/before oldest raise; leading slash stripped; set_transform reports rejections only in the log (NaN, zero quaternion, self, empty ids, data older than newest - cache, strictly); non-unit quaternions accepted; pruning uses the newest stamp from before the insert; re-parent and /tf + /tf_static duplicates behave as the review said (so they are fatal errors here). Not verified: tf2's per-frame 1,000,000 sample cap (not mirrored; a lookup disagreeing would show as tf_mismatch).

### Result
- 258 tests in bag_analysis pass via build.sh + test.sh (0 failures, 2 skipped, both pyxtf-not-installed skips that predate this).
- Real bag 2026-08-25T13-59-09: 267561 pings written (as before), dropped no_tf=0 tf_gap=0 tf_late=1 tf_mismatch=0, unpaired=30. Against the pose interpolated at the ping time (independent code): heading max 0.010 deg, position max 3 mm (2 pings above 0.001 deg, both in 5 ms stamp bursts inside one 10 ms XTF time cell, matching ambiguity); old code on the same pings: heading median 0.10 deg, max 6.6 deg, position max 0.25 m. Split parts join byte for byte to the --no-split file. Time/peak RSS: old 2:54/83.9 MB, new 2:58/86.7 MB (split 2:52/87.2 MB). Offsets +0.05 and -0.05 on a 3 min slice: written time moves -50/+50 ms, poses match truth at stamp - offset, ComputerClock fields equal the raw stamp. Details: .agent/scratchpad/xtf_split/issue100/RESULT.txt.

### Deviations and notes
- Golden file regenerated: the ComputerClock bytes (236..239 of every ping header) are now written on every ping, offset 0 included, so the file means the same whatever the option. Checked against the old file: only those bytes differ, and they equal the ping time at offset 0. Poses unchanged (the fake sends /tf at the ping stamp).
- One commit (22fa965) holds hold queue, option rename, offset and written time, because they share the same functions; the writer field is its own commit.
- Counters: stale_tf became tf_gap; added tf_late (no transform past the time within --tf-wait, or bag ended) and tf_mismatch. Wait expiry is tf_late, not stale_tf.
- Added --tf-wait (default 5 s, must exceed --max-tf-gap), cap of one hour on the offset, allow_abbrev=False on both CLIs (so --max-tf cannot reach the removed option; it also ends other prefix abbreviations).
- contacts_to_geojson: --max-tf-gap default 1.0 s (same bar as bag_to_xtf), geo_status exact/no_tf/tf_gap/tf_late/tf_mismatch.
- Known limit, documented in tf_lookup and README: a transform that arrives after a transform with a later stamp for the same frame is filed in order, but a ping already answered from the wider bracket is not revisited.
- Real-data chain is all-dynamic for three links (map, base_link_north_up, base_link); the bag has no re-parent or /tf + /tf_static duplicates (the run would have stopped).

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-11-17 14:40 -05:00
**By**: Claude Code Agent (Claude Sonnet)
**Verdict**: approved

**Branch**: feature/issue-100 at `fdfd886`
**Mode**: pre-push
**Depth**: Deep (reason: ~2200 changed lines, survey-georeferencing path)
**Must-fix**: 0 | **Suggestions**: 9
**Round**: 1 | **Ship**: recommended — no must-fix; tf2 bookkeeping verified by 144k-query differential fuzz and by 2 independent reads; suggestions are optional. Specialists: static (tests incl. flake8: 258 pass, 2 skipped), governance/plan, 2 Claude adversarial lenses, Gemini; Codex unavailable (usage limit).

### Findings
- [x] (suggestion) Port/starboard pairing across a dropped mate: a ping dropped for tf can leave its mate pending, which then pairs with the next opposite-channel ping (0.1 s later, inside the 0.25 s tolerance) using that ping's pose and time; also each tf drop adds an `unpaired` — `bag_to_xtf.py:505-524`
- [x] (suggestion) Negative offset not checked against `--tf-wait`: lookup time is stamp+|offset| but wait runs from record time; offset -6 with default wait makes every ping `tf_late` (loud, opaque cause). Require wait > gap + max(0,-offset) or document — `bag_to_xtf.py:301`
- [x] (suggestion) Cycle check in `TfChain.check` runs after a DEFINITE verdict returns, so a cyclic tree gives no_tf/tf_gap rather than TfChainError (counted drop, never a wrong pose) — `tf_lookup.py:198-215`
- [x] (suggestion) `cache_ns` uses round() while `Duration(seconds=)` truncates; build the Buffer with `Duration(nanoseconds=self.cache_ns)` — `tf_lookup.py:130`
- [x] (suggestion) Duplicate stamp with a different value: tf2 replaces the value silently (probed); tracker cannot see it. Count a re-fed identical stamp in `rejected` — `tf_lookup.py:_Stamps.add`
- [x] (suggestion) Cache eviction reported as `no_tf` ("before the first sample"); `contacts_to_geojson` also never prints `chain.rejected` and its 6 h cache gives misleading no_tf for older log entries in a long bag — `tf_lookup.py:_check_link`, `contacts_to_geojson.py:main`
- [x] (suggestion) Option edge cases: `--tf-cache 1e30` gives an uncaught OverflowError; `--max-tf-gap` below 0.5 ns rounds to 0; past `--end-time` non-TF messages skip the wait check so held pings only settle at bag end (scan cost, result unchanged) — `bag_to_xtf.py:_parse_args, _iter_pings`
- [x] (suggestion) Test truth for hundredths/ComputerClock repeats the production formula; add integer-ns boundary cases (x.009999999, x.999999999) — `test_xtf_pose_time.py` offset test
- [x] (suggestion) README: the "98% ... 0.05 s median, 0.23 s" figure has no cited source (RESULT.txt gives 524684/535122 written pings); note `unpaired`/`malformed` in the drop table, that TF is no longer consumed after `--end-time` once the queue empties, and that `earth` must be an ancestor of the frame — `README.md:~158, ~207`

## Implementation
**Status**: complete
**When**: 2026-11-17 14:10 -05:00
**By**: Claude Sonnet

**Branch**: feature/issue-100 at `0c5d5ca`
**Addressed**: Local Review (Pre-Push), round 1, 2026-11-17 14:40 -05:00 (at `fdfd886`): all 9 suggestions, none deferred
**Commits**: `8016a3d` `79a7cb9` `ccc92c3` `f78df0c` `0c5d5ca`

### Actions
- [x] Suggestion 1, mispairing across a dropped mate (treated as must-fix) — `bag_to_xtf.py:_pair_by_stamp, _HeldPing, _ping_verdict, _resolve` (`8016a3d`). Reproduced first: `test_a_ping_with_one_unresolvable_half_is_dropped_whole` wrote 3 pings instead of 2 on the old code (the surviving port half of ping A paired with the starboard half of ping B, 0.14 s later). Pairing is now decided on the stamps alone, before any lookup; a pair whose pose cannot be found is dropped whole and counted once under its reason; its halves are never left pending and are not also `unpaired`. Golden and split-join tests pass unchanged. Counters: `no_tf`, `tf_gap`, `tf_late`, `tf_evicted`, `tf_mismatch`, `paired` count pings; `port/starboard msgs`, `malformed`, `unpaired`, `out_of_window` count messages; the report prints two lines that say so; plan step 4 states it.
- [x] Suggestion 2, negative offset vs `--tf-wait` — `bag_to_xtf.py:_iter_pings`, `--tf-wait` help (`ccc92c3`). The wait is `--tf-wait + max(0, -offset)`; tests: offset -6 writes all 60 pings with the pose at stamp + 6 s; with no transforms it is still `tf_late`.
- [x] Suggestion 3, loop before verdict — `tf_lookup.py:TfChain.check` (`79a7cb9`). The chain is walked to its end before a verdict is returned; a loop raises whatever the earlier links say. A link with no samples yet is now `wait_chain` (it was ignored before).
- [x] Suggestion 4, one cache length — `tf_lookup.py:TfChain.__init__` (`79a7cb9`): `Buffer(cache_time=Duration(nanoseconds=cache_ns))`; test with 10.0000000006 s agrees with bare tf2. `--tf-cache` above 1e7 s (the OverflowError case) and a `--max-tf-gap` that rounds to 0 ns are usage errors in both tools; also unparsable `--start-time`/`--end-time` (nan, inf, text).
- [x] Suggestion 5, re-fed stamp — `tf_lookup.py:_Stamps.add, _feed_one` (`79a7cb9`): counted as `duplicate_stamp` in `rejected` (still handed to tf2, which keeps the last value); the note now reads "transforms rejected or re-fed" and `contacts_to_geojson` prints it too.
- [x] Suggestion 6, eviction — new verdict `tf_evicted` (own counter, `geo_status`, README row); `contacts_to_geojson` also reports a loop as an error (exit 1) and warns naming `--tf-cache` (`79a7cb9`).
- [x] Suggestion 7, options edges and `--end-time` — option edges as in 4; after `--end-time` every message advances the clock so held pings settle on the wait limit and the bag is not read to its end (`ccc92c3`); test counts messages consumed.
- [x] Suggestion 8, integer-nanosecond boundaries — `test_hundredths_are_cut_from_the_integer_nanoseconds` (six cases, expected bytes worked out by hand from 1_750_000_000 s = 2025-06-15 15:06:40 UTC) (`f78df0c`). **This found a real defect**: 5 of the 6 cases failed. Times were made with `fromtimestamp(ns / 1e9)`, a float with about 240 ns of error that rounds to microseconds, so x.009999999 and x.999999999 landed on the wrong hundredth or second. Fixed with `_utc_from_ns` (integer arithmetic, truncated). On the real bag 17 of 267561 pings change, in the HSeconds byte of the time and of ComputerClock only.
- [x] Suggestion 9, README — before-fix figures cited to issue #100, `unpaired`/`malformed` and `tf_evicted` in the drop tables, per-ping vs per-message wording, the whole-bag TF sentence corrected for `--end-time`, `earth` must be an ancestor of the ping's frame (`0c5d5ca`).

### Result
- `build.sh` + `test.sh bag_analysis`: 288 tests, 0 failures, 2 skipped (the same two pyxtf skips); flake8 and pep257 included; no rule disabled.
- Real bag 2026-08-25T13-59-09, whole bag (RESULT.txt, "Round 2"): 267561 pings written (same as round 1); dropped pings no_tf=0 tf_gap=0 tf_late=0 tf_evicted=0 tf_mismatch=0; messages malformed=0 unpaired=31 (round 1 per message: tf_late=1 unpaired=30; the lone half is now just unpaired, as the old code counted it). Residual against interpolated truth unchanged: heading max 0.010 deg, pitch max 0.031, roll max 0.025, position max 3 mm. Default split: 5 parts of 58663, 58812, 58704, 58708, 32674 pings; ping bytes of the parts joined are byte for byte the --no-split file. Large outputs deleted.

### Not done / not verified
- A single duplicated stamp with a different value is surfaced (counted), not corrected; the tracker holds stamps, not values.
- `--end-time` is still decided on one message's stamp: a single bad future stamp on a sonar message stops admission. Not part of the review; unchanged.
- tf2's per-frame 1,000,000-sample cap is still not mirrored (as in round 1).
