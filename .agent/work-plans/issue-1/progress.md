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
