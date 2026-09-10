---
issue: 82
---

# Issue #82 — M3 and DeltaT drivers publish no beamwidths — follow the garmin_sidescan precedent, after the consumer unit fix

## Issue Review
**Status**: complete
**When**: 2026-09-10 14:12 -04:00
**By**: Claude Code Agent (Claude Sonnet 5)

**Issue**: #82
**Comment**: (best-effort post follows this entry; not recorded inline)
**Scope verdict**: needs-splitting

### Actions
- [ ] **Blocking dependency not yet satisfied**: the issue's own "Ordering — do not populate first" gate is `cube_bathymetry#144`. That is now `cube_bathymetry#153` (an open PR), verified `state: OPEN`, `mergeable: MERGEABLE`, `mergeStateStatus: UNSTABLE` — **not merged**. Do not begin populating M3/DeltaT beamwidths (or merge a PR that does) until #153 merges; populating in radians against the pre-#153 consumer produces the *opposite* 57x error the issue is trying to fix. plan-task should record this as an explicit pre-implementation gate, not just an ordering note.
- [ ] **Split by repo**: `imagenex_deltat` (the DeltaT driver) is **not in the marine_tools repo** — it is its own repo, `rolker/imagenex_deltat` (checked out at `layers/main/sensors_ws/src/imagenex_deltat`, origin `github.com/rolker/imagenex_deltat`). It currently has zero PingInfo/beamwidth code of any kind (confirmed by grep — the issue's "nothing" characterization is accurate). A single PR cannot span both repos, so this issue's scope cannot be completed as filed. Recommend: scope #82 to the M3/`kongsberg_em_bridge` half only (stays in marine_tools, where the issue lives), and file a sibling issue in `rolker/imagenex_deltat` for the DeltaT half, cross-referenced "Part of #82". Checked: no existing beamwidth issue in that repo (`gh issue list --repo rolker/imagenex_deltat --search beamwidth` → empty).
- [ ] **Datasheet figures may not be obtainable from workspace state alone**: no sourced M3 or Imagenex DeltaT beamwidth figure exists anywhere in the workspace today. Checked `cube_bathymetry`'s `Device` struct (`error_model.h:38,41`) — only a generic uncited 2.0° fallback default, no per-device table or citation. No `.agent/knowledge` doc, README, or comment anywhere carries either device's datasheet numbers. This should be flagged as an open question to the operator (does an M3/DeltaT datasheet exist to cite?) rather than assumed solvable in-session — the acceptance criteria's "cite a datasheet figure and its conditions" bar cannot be met by searching the repo alone.
- [ ] **Stale-comment fix needs a live citation, not just deletion**: `kongsberg_em_bridge/kongsberg_em_bridge/node.py:547-550` cites `cube_bathymetry#30` (the closed umbrella validation issue), not `#144`/`#153` (the actual unit-bug fix). The issue's Acceptance section already asks for this comment to be replaced with what is true — plan-task should have it reference `cube_bathymetry#153` (or `#144`) once merged, and state plainly that beamwidths are now safe to populate because the consumer reads radians correctly.
- [ ] **Doc consequence**: `garmin_sidescan/README.md:140-176` documents its beamwidth table and the empty/uncharacterised convention; `kongsberg_em_bridge/README.md` has no equivalent section today. Populating beamwidths there is a documented-behavior change (consequences map: "Package parameters, topics, or services declared in node code → update the package README") and should get the same README treatment in the same PR.

### Findings detail (for the posted comment)

**Scope Assessment**
- Well-scoped? No — see repo-split finding above; the DeltaT half belongs to a different repo than the one the issue is filed in.
- Right repo? Partially. The M3/`kongsberg_em_bridge` half and the `garmin_sidescan` precedent both correctly live in `marine_tools`. The DeltaT half does not — `imagenex_deltat` is a separate repo (`rolker/imagenex_deltat`).
- Dependencies: `cube_bathymetry#153` (open PR fixing `cube_bathymetry#144`) — hard blocker, not yet merged, verified live via `gh pr view 153 --repo rolker/cube_bathymetry`.

**Verified against source (not assumed from the issue body)**
- `kongsberg_em_bridge/kongsberg_em_bridge/node.py:544-548`: the `tx/rx_beamwidths left empty on purpose` comment is present verbatim as quoted in the issue, citing `cube_bathymetry#30` (stale — should cite #144/#153 post-merge).
- `garmin_sidescan/garmin_sidescan/node.py:88-135,851-858`: confirmed the precedent is real and matches the issue's description — a `(generation, side) -> radians` table, `None` (empty field) for uncharacterised generations, docstring warning about the exact cube#30/rviz_sonar_image consumer bugs, and an existing unit-test precedent (`garmin_sidescan/test/test_node.py`) covering table coverage, unknown-generation, and partial-coverage cases — a template the M3/DeltaT work should follow test-for-test.
- `imagenex_deltat` repo (`layers/main/sensors_ws/src/imagenex_deltat`): confirmed zero references to `PingInfo`, `beamwidth`, `rx_beamwidths`, or `tx_beamwidths` anywhere in the driver.
- `cube_bathymetry` PR #153 (github.com/rolker/cube_bathymetry/pull/153): confirmed open, not merged; confirmed its description matches the summary given in this handoff (boundary-normalize beamwidth units to radians once in the `Device` constructor, validate per-beam values as finite/positive/<pi before trusting them over the fallback, plus unrelated attitude-units and Calder-porting fixes). `mergeStateStatus: UNSTABLE` at time of review — do not treat as landed.
- `rolker/ros2sonic#1` (transmit fan in the receive field) confirmed open — a live example of the exact failure mode the issue's "empty rather than guessed" rule guards against; correctly cited as context only, not in scope here.

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Workspace vs. project separation | Action needed | DeltaT work belongs in `rolker/imagenex_deltat`, not `marine_tools` — split required |
| A change includes its consequences | Action needed | `kongsberg_em_bridge/README.md` needs the same beamwidth-documentation treatment `garmin_sidescan/README.md` already has |
| Capture decisions, not just implementations | Watch | The stale `#30` citation shows a prior fix (garmin_sidescan #62) didn't get the node.py comment's cross-reference kept current when the tracking issue changed; worth a passing note in the PR, not a blocker |
| Test what breaks | OK | `garmin_sidescan/test/test_node.py` gives a direct, reusable test template (table coverage / unknown-device / partial-coverage) for the M3 and DeltaT tables |
| Only what's needed | OK | Issue shape (per-device table, empty-if-uncharacterised) matches the existing precedent; no scope inflation beyond that |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| 0008 — ROS 2 conventions | No | No new packages, launch files, or interface changes |
| 0003 — Project-agnostic workspace | N/A | Both target repos are project repos, not the workspace repo |
| (none else) | — | No workspace-repo or CI/branch-protection changes proposed |

### Recommendations

1. Do not schedule plan-task/implementation for #82 until `cube_bathymetry#153` merges — this is a hard ordering gate, not a soft preference.
2. Re-scope #82 to the M3 (`kongsberg_em_bridge`) half only; open a new issue in `rolker/imagenex_deltat` for the DeltaT half, referencing "Part of #82".
3. Surface the datasheet-availability question to the operator explicitly before implementation starts — it may block the "cited figure" acceptance criterion for one or both devices even after the ordering gate clears.
4. When the comment at `node.py:547-550` is rewritten, cite the merged `cube_bathymetry#153` PR (or `#144`), not the stale `#30`.
5. Add a `kongsberg_em_bridge/README.md` beamwidth section mirroring `garmin_sidescan/README.md:140-176` in the same PR.
