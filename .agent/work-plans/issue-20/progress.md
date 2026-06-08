---
issue: 20
---

# Issue #20 — garmin_sidescan: ship a generic example launch (move BizzyBoat specifics out of the driver)

## Integrated Review
**Status**: complete
**When**: 2026-06-07 21:29 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))

**PR**: #21 at `a998f05`
**Sources**: 2 Copilot reviews (latest @ `a998f05`)
**Cross-source confirmations**: 0
**CI**: copilot check pass; no build-and-test visible (marine_tools gate #18/#19 may be unmerged)

### Findings
- [ ] (valid, Copilot) install_proxy_service.ps1 + tools/README default logs to `C:\project11\logs` — project-specific in a generic repo; use the ProgramData convention (`$env:ProgramData\GarminProxy\logs`) — `garmin_sidescan/tools/install_proxy_service.ps1`, `tools/README.md`
- [ ] (minor, Copilot) example launch docstring names the downstream `bizzyboat_project11` wrapper; a shared driver shouldn't hardcode a consumer — keep the generic split reference only — `garmin_sidescan/launch/garmin_sidescan.launch.py:10`

### False positives
- (Copilot, x3) frame_id should default to gcv_sonar per issue #20 — the rename to garmin_sidescan was an explicit user instruction superseding the issue text ("don't call it gcv… most people won't have a clue"); launch arg, node default, and docstring all consistently say garmin_sidescan, so there is no real mismatch. Copilot is enforcing the stale pre-rename contract.
