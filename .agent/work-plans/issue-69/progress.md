---
issue: 69
---

# Issue #69 — kongsberg_em_bridge: parse N/78 signal length and publish SonarInfo (pulse length for GeoCoder correction)

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-07-16 11:12 -0400
**By**: Claude Code Agent (Claude Fable 5)
**Verdict**: changes-requested (all findings addressed in `47b0007` same session)

**Branch**: feature/issue-69 at `23b1e02` (fixes at `47b0007`)
**Mode**: pre-push
**Depth**: Deep (reason: 334 changed lines; recv-thread/timer concurrency surface)
**Must-fix**: 1 | **Suggestions**: 4
**Round**: 1 | **Ship**: recommended — the single must-fix was a stamp-clock consistency fix, applied and re-verified (31/31 tests + end-to-end smoke: change detection, heartbeat, late-joiner latching, no stray processes)

### Findings
- [x] (must-fix) heartbeat stamped from the system clock while pings/change-publishes use the sonar 1PPS clock — clock divergence could place heartbeats after a segment's pings, silently breaking the at-or-before association rule in data-of-record bags — `kongsberg_em_bridge/node.py` (Claude Adversarial / Lens A + Governance, cross-confirmed) → heartbeat now re-publishes with the change's original stamp (rosbag2 segments by receive time; no re-stamp needed)
- [x] (suggestion) heartbeat timer callback unguarded where the recv-thread publish is deliberately guarded (shutdown race) — guard added (Lens B + Governance, cross-confirmed)
- [x] (suggestion) 'sonar_info updated' log unthrottled; alternating multi-mode pinging changes the signature every ping → console flood — throttled 30 s like the sibling decode-health line (Lens B)
- [x] (suggestion) sonar_info_period doc overclaimed the split-segment guarantee (size-based splits can be shorter than the period) — relationship documented (Lens B)
- [x] (suggestion) new topic/param undocumented, package had no README — README added covering topics/services/params/tools (Governance)

Cleared by review (explicitly checked, sound): sector struct offsets vs the 24-byte layout; float exact-equality in the signature (raw f32 pass-through, no arithmetic); signature covers every variable message field; first-ping ordering (SonarInfo published before the detections it describes); ntx=0 and unmapped-waveform honesty; recv-vs-timer locking; cross-layer marine_interfaces dep (cube_bathymetry precedent). Static analysis: ament flake8/pep257 via colcon test (2 earlier nits fixed pre-review). Plan drift: none; plan updated inline with the stamp-semantics change.

Verification: 31/31 package tests green; end-to-end smoke (scratchpad, live node over UDP): phase 1 change-detection with heartbeat disabled (1 publish per distinct state, none for identical pings, honest sentinels on the wire), phase 2 heartbeat re-publish with preserved stamp + late transient_local joiner receives the latch.
