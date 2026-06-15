---
issue: 41
---

# Issue #41 — garmin_sidescan: remove sound-speed interlock + fix near-field blanking (sample0) in range encoding

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-15 01:25 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: changes-requested

**Branch**: feature/issue-41 at `1d0f774`
**Mode**: pre-push
**Depth**: Standard (reason: sensor-driver node, transmit-control + message-contract change, 207+/363- across 11 files)
**Must-fix**: 2 | **Suggestions**: 5

### Findings
- [x] (must-fix) Dry-transducer auto-stop premise cited nowhere → **resolved (docs)**: provenance (R. Arsenault field obs) + bench-confirm TODO recorded in gcv_protocol.md §4.6; README states it's unconfirmed. Bench-confirm stays a tracked TODO in the protocol doc — `garmin_sidescan/docs/gcv_protocol.md`
- [x] (must-fix) transmit_on_startup unconditional ON → **resolved (docs)**: README + param table warn that startup/commanded transmit is unguarded; only energize submerged. (User decision: keep interlock removed, document the operational constraint.) — `garmin_sidescan/README.md`
- [x] (suggestion) sample0 nonzero is a breaking RawSonarImage wire-contract change → **tracked as rolker/marine_tools#42** (audit rqt waterfall / CAMP); called out in the PR body
- [ ] (suggestion) ping_info.sound_speed changed from measurement (0.0 when stale) to fixed nominal 1500; semantic shift documented in README + PR body — `garmin_sidescan/garmin_sidescan/node.py:796`
- [ ] (suggestion) transmit diagnostic dropped the "maybe-dry-transmitting" WARN; accepted as a consequence of the interlock removal — `garmin_sidescan/garmin_sidescan/node.py:855`
- [x] (suggestion) bins>GRID_BINS warning understated the consequence (scale wrong for over-length tail) — reworded — `garmin_sidescan/garmin_sidescan/node.py:813`
- [x] (suggestion) parse_subheader docstring said bin size = display_range/n_bins; corrected to /GRID_BINS — `garmin_sidescan/garmin_sidescan/decode.py:410`

### Resolution (pre-push, before push)
Two doc commits address the review: `5c59036` records the auto-stop premise
provenance + transmit_on_startup caveat and the two Lens A doc nits. Remaining
open items are intentional consequences (sound_speed nominal, dropped WARN) or
tracked separately (#42). Bench-confirm of the auto-stop latency stays a TODO in
gcv_protocol.md §4.6. Cleared to push + open PR.
