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
