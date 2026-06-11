---
issue: 16
---

# Issue #16 — garmin_sidescan: obtain nadir depth (chartplotter broadcast vs ClearVu bottom-detection)

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-11 (local)
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: approved
**Branch**: feature/issue-16 at `24f1bf6`
**Mode**: pre-push
**Depth**: Standard (reason: core decode.py change + new tool, ~900 lines)
**Must-fix**: 1 (fixed) | **Suggestions**: 3 (2 fixed, 1 documented design trade-off)

### Findings
- [x] (must-fix) waterfall tool render() IndexError when window has no side-scan pings (n=0) — fixed, `tools/sidescan_waterfall.py`
- [x] (suggestion) read_pings dropped the final accumulated run — added asm.flush() — `tools/sidescan_waterfall.py`
- [x] (suggestion) port/starboard index pairing fragility — added count-mismatch warning — `tools/sidescan_waterfall.py`
- [ ] (suggestion) strip_first_layer_trailer delimiter can over-cut on coincidental `52 80 10`+`0x43` in samples — documented design trade-off (no length prefix available), `decode.py`
