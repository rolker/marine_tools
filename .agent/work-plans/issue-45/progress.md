---
issue: 45
---

# Issue #45 — bag_analysis: convert recorded Garmin sidescan bags to XTF

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-15 23:42 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: approved (findings addressed)

**Branch**: feature/issue-45 at `9f5bd12`
**Mode**: pre-push
**Depth**: Standard (reason: ~960 lines new code, binary-format + geodesy correctness)
**Must-fix**: 4 | **Suggestions**: 3

### Findings
- [x] (must-fix) Bounded the silent latest-TF fallback; drop+count stale pings — `cli/bag_to_xtf.py:_lookup_pose`
- [x] (must-fix) Atomic output (.partial + rename) so a crash leaves no truncated XTF — `cli/bag_to_xtf.py:main`
- [x] (must-fix) Exit non-zero when zero pings written — `cli/bag_to_xtf.py:main`
- [x] (must-fix) Stop writing garbage per-ping Frequency uint16; CHANINFO float authoritative — `xtf/writer.py:_chan_header`
- [x] (suggestion) Pair/timestamp/speed on header.stamp not bag-receive time — `cli/bag_to_xtf.py:_make_pending`
- [x] (suggestion) Drop sample_rate<=0 pings as malformed — `cli/bag_to_xtf.py:_convert`
- [x] (suggestion) Report per-cause drops + nadir-absent altitude warning — `cli/bag_to_xtf.py:_report`
- [ ] (rejected) Write geodetic alt to SensorDepth — alt is ellipsoidal height, not depth-below-surface; SensorDepth=0 correct for surface mount
