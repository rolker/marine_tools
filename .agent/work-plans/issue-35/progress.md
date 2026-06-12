---
issue: 35
---

# Issue #35 — garmin_sidescan: publish per-channel range scale from sub-header v2 (sample_rate currently 0 or wrong under auto-range)

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-11 21:12 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: approved (findings addressed in 1c91f34)

**Branch**: feature/issue-35 at `1c91f34`
**Mode**: pre-push
**Depth**: Standard (reason: ~600 lines / 11 files, sensor-driver publish path)
**Must-fix**: 1 | **Suggestions**: 7 (1 Copilot false positive dropped)

### Findings
- [x] (must-fix) replay tool: --rate <= 0 unvalidated divisor — `tools/replay_debug_raw.py:85` (cross-model: Claude + Copilot)
- [x] (suggestion) down-look must not take commanded-range fallback (~2x wrong scale) — `garmin_sidescan/node.py` (cross-confirmed: adversarial + governance)
- [x] (suggestion) nadir max_range should be the ping's own v2, not the side-scan command clamp — `garmin_sidescan/node.py`
- [x] (suggestion) verify tool: filter RawSonarImage topics by name (M3 pollution) — `tools/verify_range_scale.py`
- [x] (suggestion) verify tool: anchor window to first raw datagram (portability) — `tools/verify_range_scale.py`
- [x] (suggestion) verify tool: missing assembler flush — `tools/verify_range_scale.py`
- [x] (suggestion) tools/README: best-effort QoS flag on topic-echo example — `tools/README.md`
- [x] (governance) platform follow-up for _nadir URDF frame + nadir_depth recording + freq params — filed rolker/unh_echoboats_project11#254
- Copilot claim that replay t0 anchors to any topic: false positive (loop continues on non-raw topics before t0 is set)

Verification: 64/64 tests; ament flake8/pep257 clean; end-to-end UDP replay of the 2026-06-10 Cod Rock bag through the real node — consumer recovered port/stbd {47.8, 30.4} m across the auto-range step, down {10.8, 11.2} m, nadir_depth 7.62–7.76 m on the shoal.
