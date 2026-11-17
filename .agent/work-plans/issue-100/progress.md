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
