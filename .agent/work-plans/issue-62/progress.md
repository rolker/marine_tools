---
issue: 62
---

# Issue #62 — garmin_sidescan: populate `ping_info` frequency + beamwidth from generation lookup table

## Plan Authored
**Status**: complete
**When**: 2026-06-21 04:12 +00:00
**By**: Claude Code Agent (Claude Sonnet)

**Plan**: `garmin_sidescan/.agent/work-plans/issue-62/plan.md` at `ec83ed7`
**Branch**: feature/issue-62 at `ec83ed7`
**Phases**: single

### Open questions
- [ ] No open questions — plan is review-plan-ready.

## Plan Review
**Status**: complete
**When**: 2026-06-21 04:32 +00:00
**By**: Claude Code Agent (Claude Opus)  <!-- independent: fresh-context dispatch, different model than the Sonnet plan author; the shared "Claude Code Agent" name collides across all workspace agents, so the name-match self-review heuristic is a false positive here -->

**Plan**: `.agent/work-plans/issue-62/plan.md` at `e237e6f`
**PR**: PR-less (--issue / file path mode; gh unauthenticated in this worktree — issue body/comments not fetched)
**Verdict**: changes-requested

### Findings
- [ ] (must-fix) README `Protocol notes` (`README.md:117-121`) documents the *opposite* decision — frequency deliberately NOT derived "to avoid baking in a transducer assumption"; plan reverses this but only updates the param-table row. Must rewrite the paragraph or the README self-contradicts. — `plan.md:134-141`
- [ ] (must-fix) Central `rx_beamwidths` convention is unverified and contradicted in-repo: `rviz_sonar_image` (claimed `±` half-angle consumer) is not in the tree; `cube_bathymetry` CUBE uses full across/along narrow directivity beamwidths (`device.c:614`), not a 55° fan half-angle; sibling `kongsberg_em_bridge` (`node.py:345-347`) deliberately leaves `rx_beamwidths` empty so CUBE uses its tuned Device beamwidth — stamping 0.48 rad would corrupt CUBE footprint/error. Cite real consumer + reconcile before implementing. — `plan.md:55-60,88-92,121-128`
- [ ] (suggestion) Override asymmetry: frequency has `freq_*_hz` override, beamwidth has none; a non-GT34UHD-TM GCV-20 gets a wrong beamwidth with no escape hatch. Add a `beamwidth_*_rad` override or loudly document the transducer-specific assumption. — `plan.md:117-119`
- [ ] (suggestion) `rx_beamwidths` semantic is a cross-package interface decision (cube_bathymetry, kongsberg, any RawSonarImage consumer); plan marks ADR-0001 "marginal". Per ADR-0008 + consequences map, record an ADR or explicit cross-package note. — `plan.md:197`
- [ ] (minor) GCV-20 SideVü band midpoint of 1,060-1,170 kHz is 1,115 kHz; plan uses 1,120,000 Hz — within band, documented as marketing-rounding, acceptable. — `plan.md:76-77`
