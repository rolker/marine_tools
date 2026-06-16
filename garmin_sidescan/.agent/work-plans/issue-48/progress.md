---
issue: 48
---

# Issue #48 — garmin_sidescan: extract render layers by grammar length, not magic/trailer search (regression of #26)

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-16 01:10 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: approved

**Branch**: feature/issue-48 at `383b7d9`
**Mode**: pre-push
**Depth**: Standard (reason: parsing-logic change to a deployed sensor driver, ~280 lines)
**Must-fix**: 0 | **Suggestions**: 2

### Findings
- [ ] (suggestion) Zero-length / zero-count render-layer field is treated as "no layer" (returns `b''`); semantics unspecified and untested — `decode.py:_first_render_layer`
- [ ] (suggestion) New `SUBHEADER_OPENER` guard is stricter than the old FH-find: a packet with a garbled `01 03 09` opener now yields `b''` instead of a heuristic partial parse (assessed safe — 697k frames zero exceptions; arguably an improvement) — `decode.py:_first_render_layer`

### Notes
- Two disjoint-lens Claude Adversarial passes (logic + systemic). Both initially raised bounds-check "must-fix" items, then self-retracted: Python slicing never throws, LEB128 is unsigned, and every malformed/truncated path returns `b''` cleanly. No real must-fix.
- Static analysis: colcon test lint (flake8 + pep257) clean; 72 tests pass (0 failures).
- Confirmed: no dangling references to removed `strip_first_layer_trailer` / `TRAILER_*`; published sample shape/dtype unchanged; `PingAssembler` leading-header strip, `generation_from_layers`, and `dark_layer` (GCV-10) all intact.
