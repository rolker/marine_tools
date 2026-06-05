---
issue: 1
---

# Issue #1 — Add node to ingest M3 Kongsberg EM datagrams → marine_acoustic_msgs/SonarDetections

## Local Review (Post-PR)
**Status**: complete
**When**: 2026-06-04 20:52 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: changes-requested (all addressed in 733b3b6)

**PR**: #14 at `733b3b6`
**Mode**: post-PR
**Depth**: Deep (network binary parsing + background thread)
**Must-fix**: 1 | **Suggestions**: 3

### Findings
- [x] (must-fix) recv thread died silently on a publish-time exception; split parse (narrow catch) from publish (broad guarded catch) — `kongsberg_em_bridge/node.py:_recv_loop`
- [x] (suggestion) _stamp nanosec could round to 1e9; carry into sec — `kongsberg_em_bridge/node.py:_stamp`
- [x] (suggestion) parse_xyz88 validity (det_info<16 → bit7) and wrong det_info offset (15→16); added test — `kongsberg_em_bridge/em_datagrams.py:parse_xyz88`
- [x] (suggestion) publish-after-shutdown race — subsumed by the must-fix
- Note: Copilot Adversarial timed out at 300s mid-investigation of the nanosec finding (cross-model corroboration); static analysis clean via ament_flake8/pep257.

## Integrated Review
**Status**: complete
**When**: 2026-06-04 21:29 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))

**PR**: #14 at `d5b5891`
**Sources**: 4 (Copilot R1 @ `f912a5c`, R2 @ `a2be7fe`, R3 @ `11310c8`, Local Review @ `11310c8`)
**Cross-source confirmations**: 1
**CI**: copilot-pull-request-reviewer success (no build/test CI on repo)

### Findings
- [x] (cross-confirmed: Copilot R3 + Local Review) parse_xyz88 robustness — validity bit + det_info offset (prior commit) and silent truncation now raises — `kongsberg_em_bridge/em_datagrams.py`
- [x] (valid, Copilot R2) em_time_to_unix didn't validate time_ms → wild stamp on corruption; now range-checked — `em_datagrams.py`
- [x] (valid, Copilot R3) parse_n78 length guard accepted trailer-less datagrams; now requires full length — `em_datagrams.py`
- [x] (valid, Copilot R1/R3) replay.py leaked the capture FD; context manager — `replay.py`
- [x] (valid, Copilot R2/R3) replay.py docstring showed wrong --ros-args usage; fixed to --file — `replay.py`

### False positives
- (Copilot R2) node.py "rx/tx_angle_sign documented but not declared" — params removed by design for the deterministic Kongsberg→SonarDetections convention mapping (commit a2be7fe); current PR body documents the lean set. Reviewed against stale state.
- (Copilot R1) em_datagrams.py "siglen unused will fail ament_flake8 (F841)" — pyflakes exempts tuple-unpack targets; colcon flake8 passes. (siglen was genuinely unused; dropped it anyway.)
