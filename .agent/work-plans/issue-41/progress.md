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
- [ ] (must-fix) Dry-transducer auto-stop premise is asserted (README/docstring/commit) but cited nowhere authoritative; gcv_protocol.md has no out-of-water/auto-stop note — record provenance (Roland's field knowledge) + ideally bench-confirm — `garmin_sidescan/README.md:103`
- [ ] (must-fix) transmit_on_startup now does an unconditional transmit ON (startup SV guard removed), so an out-of-water power-up with transmit_on_startup:=true commands a dry transmit with no in-driver protection — `garmin_sidescan/garmin_sidescan/node.py:418`
- [ ] (suggestion) sample0 becoming nonzero is a breaking RawSonarImage wire-contract change; out-of-repo consumers (rqt_marine_sonar/rqt_operator_tools waterfall, CAMP) that assumed sample0==0 must be audited — `garmin_sidescan/garmin_sidescan/node.py:825`
- [ ] (suggestion) ping_info.sound_speed changed from best-available measurement (0.0 when stale) to a fixed nominal 1500; note the semantic shift for consumers — `garmin_sidescan/garmin_sidescan/node.py:796`
- [ ] (suggestion) transmit diagnostic dropped the "maybe-dry-transmitting" WARN; with the interlock gone this removes the one operational signal for a possibly-dry transmit — `garmin_sidescan/garmin_sidescan/node.py:855`
- [x] (suggestion) bins>GRID_BINS warning understated the consequence (scale wrong for over-length tail) — reworded — `garmin_sidescan/garmin_sidescan/node.py:813`
- [x] (suggestion) parse_subheader docstring said bin size = display_range/n_bins; corrected to /GRID_BINS — `garmin_sidescan/garmin_sidescan/decode.py:410`
