---
issue: 71
---

# Issue #71 — kongsberg_em_bridge: publish the angular-response curve (with TL provenance) in SonarInfo

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-07-16 14:53 -0400
**By**: Claude Code Agent (Claude Fable 5)
**Verdict**: approved (suggestions applied in `5a335a3` same session)

**Branch**: feature/issue-71 at `5a335a3`
**Mode**: pre-push
**Depth**: Deep (reason: ~340 lines; C++-parity-critical loader)
**Must-fix**: 0 | **Suggestions**: 7
**Round**: 1 | **Ship**: recommended — no must-fix; all suggestions applied and re-verified (43/43 tests + tier-2 curve wire smoke)

### Findings
- [x] (suggestion) tier-2 header missing absorption → published 0.0 under TL_REMOVED, violating the NaN honesty contract — loader now returns Optional[float]; None → NaN (Lens A)
- [x] (suggestion) readlines() unbounded → streamed iteration closes the never-fatal gap (Lens B/Governance)
- [x] (suggestion) "mirrors exactly" overstated (stof inf/nan/hex; sort stability on duplicate angles) — docstring scopes the claim to derive-tool output with divergences listed (Lens A + Lens B, cross-confirmed)
- [x] (suggestion) test corpus missed the derive tool's actual formats (%+.3f leading plus, %.6g scientific alpha) and the missing-absorption publish path — tests added (Lens A)
- [ ] (carried to cube_bathymetry#102) duplication pointer is one-directional: add a back-reference comment in angular_response_curve.cpp to this Python mirror when the cube repo is touched (Governance)

Cleared by review: ADR-0009/uma#268 NaN obligations met and triple-tested; parallel arrays structural; producer never emits non-empty curve with UNKNOWN provenance; threading (immutable tuple set in __init__ before threads start); startup never-fatal; curve correctly excluded from acquisition signature; heartbeat bandwidth with curve ~0.1 KB/s (pre-cleared by ADR-0009); README row accurate; launch omission consistent with precedent.

Verification: 43/43 package tests; E2E smoke — node with tier-2 CSV publishes SonarInfo whose curve arrives sorted with TL_REMOVED + verbatim alpha over the wire.
