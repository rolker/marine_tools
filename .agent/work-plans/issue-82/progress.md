---
issue: 82
---

# Issue #82 — M3 and DeltaT drivers publish no beamwidths — follow the garmin_sidescan precedent, after the consumer unit fix

## Issue Review
**Status**: complete
**When**: 2026-09-10 14:12 -04:00
**By**: Claude Code Agent (Claude Sonnet)

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

## Plan Authored
**Status**: complete
**When**: 2026-09-10 14:25 -04:00
**By**: Claude Code Agent (Claude Sonnet)

**Plan**: `.agent/work-plans/issue-82/plan.md` at `cf9c25f`
**Branch**: feature/issue-82 at `cf9c25f`
**Phases**: single

### Open questions
- [ ] None blocking implementation — datasheet-figure availability is resolved by operator decision (ship the M3 uncharacterised/empty, do not guess).
- [ ] Confirm `PingInfo.rx_beamwidths`/`tx_beamwidths` array cardinality (per-beam vs per-sector) against the installed `marine_acoustic_msgs/msg/PingInfo.msg` before finalizing the `_publish()` wiring — a verify-before-code step, not a design choice.

## Plan Review
**Status**: complete
**When**: 2026-09-10 14:31 -04:00
**By**: Claude Code Agent (Claude Sonnet)

**Plan**: `.agent/work-plans/issue-82/plan.md` at `cf9c25f`
**PR**: PR-less
**Verdict**: approve-with-suggestions

### Findings
- [ ] (suggestion) Cardinality is now resolvable, not just flagged — `cube_bathymetry/error_model.cpp:277` reads `detections.ping_info.rx_beamwidths[i]` with `i` the same per-beam index used for `two_way_travel_times[i]`/`flags[i]`, i.e. `rx_beamwidths`/`tx_beamwidths` must be sized to the *published* (post-filter) beam count, not per-sector. `ros2sonic/r2sonic/src/conversions.cpp:14-18` confirms the same per-beam convention independently (`resize(num_beams)`). Update the plan's Open Questions / step 2 to state this as resolved fact rather than "confirm during implementation." — `plan.md:112-121` ("Open Questions")
- [ ] (must-fix) The plan's illustrative step-2 code sizes the array off the wrong count: `[rx_bw] * len(parsed['beams'])` uses the *raw* pre-filter beam count, but `_publish` (`node.py:554-570`) skips invalid beams when `skip_invalid_beams` is True (default), so the actually-published `msg.two_way_travel_times`/`msg.flags`/etc. are shorter than `parsed['beams']` whenever any beam is invalid. Because every element of the fill is numerically identical this doesn't misalign values today, but it leaves `rx_beamwidths`/`tx_beamwidths` a different length than every other per-beam array in the same message, which is sloppy and inconsistent with how those arrays are actually built (appended one at a time, inside the filter loop). Implementation should build the beamwidth arrays from `len(msg.two_way_travel_times)` (or append inside the same loop), not `len(parsed['beams'])`. — `plan.md:75-91` (Approach step 2)
- [ ] (suggestion) The plan's own caution that the `garmin_sidescan` template doesn't transfer cleanly deserves to be stated more concretely now that it's verified: `garmin_sidescan` publishes `RawSonarImage` (one beam per message, hence the literal singleton `[rx_bw]` at `garmin_sidescan/node.py:855-858`), while `kongsberg_em_bridge` publishes `SonarDetections` with many, variably-filtered beams per message. The only part of the precedent that actually transfers is the table/resolver *shape* (dict keyed by device variant → `None` when uncharacterised); the publish-site array construction is structurally different and needs its own pattern, not a copy of Garmin's line. Minor wording tightening, not a design change. — `plan.md:56-62` (Context)

### Central question: is the empty table worth shipping?

Yes — approve it, alongside the comment fix and README. Two things distinguish
this from ordinary speculative generality:

1. **It is not a novel pattern invented here.** `garmin_sidescan` already
   ships this exact shape in production for its own uncharacterised device
   (GCV-10: `_RX_BEAMWIDTH_RAD`/`_TX_BEAMWIDTH_RAD` omit the `('gcv10', *)`
   keys entirely, `.get()` returns `None`, fields stay empty — verified at
   `garmin_sidescan/node.py:97-107` and documented at
   `garmin_sidescan/README.md:140-176`). Applying the same convention to a
   sibling driver in the same repo is consistency with an established
   in-repo pattern, not speculative generality from nothing.
2. **It converts a claim into an enforced invariant.** The whole issue
   exists because a code comment's claim ("leaving them empty avoids a unit
   mismatch") silently went stale the moment `cube_bathymetry#144`/#153
   changed the ground truth, and nothing caught it. A comment-only fix
   repeats the same failure mode: it is another unenforced claim that can
   go stale again with no test to catch it. The resolver + the
   uncharacterised-device test make "M3 stays empty until a sourced figure
   exists" a checked fact, not prose — matching "Enforcement over
   documentation" directly, and it is exactly what the issue's Acceptance
   criterion ("An uncharacterised device leaves the fields empty, with a
   test") asks for verbatim.

The cost is genuinely small (one dict with one `None` mapping, a pure
resolver function, three small tests), so the "only what's needed" tension
is real but minor — not enough to withhold approval. If the operator instead
wants to land only the comment fix and README now and defer the table until
a datasheet figure exists, that is a legitimate, smaller alternative; I come
down on shipping the table now because of points 1 and 2 above.

### Other dimensions

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | Single package, ~3 files, matches the `garmin_sidescan` precedent's footprint |
| Issue alignment | Good | Directly implements the issue's own Acceptance items for the M3 half; DeltaT split confirmed correctly scoped to `imagenex_deltat#1` |
| File targeting | Good | `parsed['model']` is genuinely device-reported (unpacked from the N/78 header, `em_datagrams.py:87`) and distinguishable (model 30 → `kongsberg-m3`, verified via `sonar_model_name`) — the table key is readable at the point it would be stamped |
| Consequences | Good | README, stale-comment, and test consequences all captured; no missed cross-reference found |
| Documentation & instruction impact | Good | Non-silent; "None" for instruction candidates is justified (following an existing documented precedent, not establishing a new one) |
| Principle alignment | Good | See central-question discussion above |
| ADR compliance | N/A | No new packages/topics/params/interfaces triggered |
| ROS conventions | Good | No topic/QoS/parameter changes; message field usage matches `PingInfo.msg`'s documented contract |

## Implementation
**Status**: complete
**When**: 2026-09-10 14:56 -04:00
**By**: Claude Opus 5 (1M context)

**Branch**: feature/issue-82 at `e6dbeb1` (unpushed, PR-less)
**Issues**: #82 and #83 — one branch, three commits, one PR to close both
**Scope**: `kongsberg_em_bridge` only

### Commits

- `bad7c42` — plan: extend `plan.md` to cover #83, record the operator's two
  overrides (ship ahead of the `cube_bathymetry#154` gate; remove
  `skip_invalid_beams` outright, his words quoted verbatim), apply the plan
  review's must-fix, and close the cardinality open question.
- `5810ff9` — #82, beamwidths.
- `e6dbeb1` — #83, publish invalid beams.

### #82 — beamwidths (commit `5810ff9`)

- Added `_RX_BEAMWIDTH_RAD` / `_TX_BEAMWIDTH_RAD`, keyed by the `.all` model
  number (the key `sonar_model_name` already uses), plus the pure
  `_resolve_beamwidths(model)` — the table-and-resolver shape from
  `garmin_sidescan`'s `_resolve_freq_bw`, not its publish-site line (that
  driver has one beam per message; this one has many).
- Model 30 (M3) maps explicitly to `None` on both axes: uncharacterised, not
  forgotten. **No beamwidth figure was invented, estimated or derived.** The
  fields stay empty and the CUBE error model takes its documented `Device`
  fallback. Unmapped model numbers resolve the same way.
- Rewrote the stale comment at the old `node.py:544-548`: the unit-mismatch
  rationale and the closed umbrella `cube_bathymetry#30` citation are gone,
  replaced by `cube_bathymetry#144` / PR #153 and a plain statement that
  populating radians is safe now and what is missing is a cited figure.
- Extracted the message construction from the `_publish` method into a pure
  module-level `detections_from_parsed()`, mirroring the module's existing
  `sonar_info_from_parsed`. This is what makes the publish path testable
  without an rclpy node; `_publish` keeps stamping, SonarInfo change
  detection, publishing and the health heartbeat.
- **Plan review's must-fix applied**: the beamwidth arrays are appended
  inside the publish loop alongside every sibling per-beam array, never sized
  from `len(parsed['beams'])`.
- `kongsberg_em_bridge/README.md`: new "Sensor constants → Beamwidths"
  section mirroring `garmin_sidescan/README.md:140-176`.
- Tests (`test/test_detections.py`, new): M3 → `(None, None)`; unknown model
  → `(None, None)`; publish leaves both fields empty for both (#82's explicit
  Acceptance item); and, with a monkeypatched table entry, the arrays are one
  element per published beam carrying the radians value unmodified.

### #83 — publish invalid beams (commit `e6dbeb1`)

- `skip_invalid_beams` **removed entirely** — declaration, `self.skip_invalid`,
  the builder argument, and the skip branch. Per the operator's correction
  mid-task, superseding the earlier "default it to False and keep it as an
  escape hatch" instruction. Grep confirms no launch file, config or test
  referenced it.
- Comment at the old declaration site now says what is true: the driver
  reports what the sonar reported, filtering is the consumer's job, and
  `cube_bathymetry#154` is the consumer that does not yet do it — named as a
  bug we own, not a constraint to design around.
- Unconditional startup warning naming `rolker/cube_bathymetry#154` and
  stating the failure mode in words (a consumer ignoring `DetectionFlag`
  reads an invalid beam's zero travel time as a sounding at zero depth,
  seafloor at the surface). With the parameter gone this is the only
  in-band signal, and it sits in the log beside the data.
- README: parameter row removed; new "Invalid beams" section stating that
  every beam is published with its honest flag, that the consumer is **not**
  yet fixed, and naming `#154`.
- Tests: an invalid beam is published carrying `DETECT_BAD_SONAR` (and keeps
  its zero travel time) rather than vanishing; per-beam array alignment
  pinned across `flags`, `two_way_travel_times`, `tx_delays`, `intensities`,
  `tx_angles`, `rx_angles` over mixed, all-invalid and all-valid pings, and
  again with the beamwidth arrays populated.

### Operator override, recorded

`marine_tools#83`'s gate on `cube_bathymetry#154` is real and `#154` is open.
The operator decided (2026-09-10) to ship the driver first regardless: there
is no near-term plan to collect M3 data, and if any is collected before the
consumer is fixed, the resulting bad data is what will motivate fixing it.
That decision is recorded in `plan.md`, not relitigated here; no second gate
was added and the change was not hedged. His removal direction is quoted
verbatim in the plan: "Remove it, I need to keep things as lean and as clean
as practicle. Also, it's a bug that a consumer doesn't respect the flag so we
shouldn't be working around bugs we can fix ourselves."

### Build and test — real results

```
./sensors_ws/build.sh marine_tools          → 1 package finished (warnings only, pre-existing -Wsign-compare in marine_tools C++)
./sensors_ws/build.sh kongsberg_em_bridge   → 1 package finished
./sensors_ws/test.sh  kongsberg_em_bridge   → 51 tests, 0 errors, 0 failures, 0 skipped
./sensors_ws/test.sh  marine_tools          → 0 errors, 0 failures, 0 skipped
```

One intermediate failure was hit and fixed properly, not suppressed: flake8
`Q003` on an escaped apostrophe in the new warning string (switched that
line's outer quotes). No test was skipped, disabled or loosened.

### Deliberately not done

- No M3 beamwidth figure populated — none is sourced, and inventing one is
  out of bounds. The table entry exists as an explicit `None` so a future
  cited figure is a one-line change plus a table-coverage test.
- The DeltaT half of #82 stays in `rolker/imagenex_deltat#1` — different
  repo, cannot be reached from a marine_tools PR.
- `cube_bathymetry#154` itself is untouched; it is the consumer's fix and
  belongs to that repo.
- Nothing pushed, no PR opened — the host does that. The PR body carries the
  closing keywords for both #82 and #83; the commits deliberately do not.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-14 09:10 -04:00
**By**: Claude Code Agent (Claude Fable 5.1)
**Verdict**: changes-requested

**Branch**: feature/issue-82 at `0575194`
**Mode**: pre-push
**Depth**: Standard (reason: driver output contract change, ~400 lines, parameter removed)
**Must-fix**: 2 | **Suggestions**: 9
**Round**: 1 | **Ship**: continue — two mechanical must-fixes; expect round 2 to ship

### Findings
- [ ] (must-fix) heartbeat `N detections (of nrx beams)` is tautological now every beam is published; log `nvalid` vs `nrx` — `kongsberg_em_bridge/kongsberg_em_bridge/node.py:677`
- [ ] (must-fix) README + node.py cite #82 as the follow-up tracker for the M3 figure while the PR closes #82; file a follow-up and cite it, or keep #82 open — `kongsberg_em_bridge/README.md:77`, `node.py:113`
- [ ] (suggestion) cube#154 hazard also hits offline importers (import_bag, batch_regen, bag_to_geotiff) → store contamination; say "any CUBE ingest path" and name the tools on cube#154 — `kongsberg_em_bridge/README.md:25`
- [ ] (suggestion) startup warning has no retirement condition; add "remove driver warning" as acceptance item on cube#154 — `node.py:422`
- [ ] (suggestion) garmin_sidescan still cites the retired cube#30 degrees hazard — `garmin_sidescan/README.md:174`, `garmin_sidescan/garmin_sidescan/node.py:128`
- [ ] (suggestion) `_resolve_beamwidths` should reject non-positive table values (README promises never zero-filled) — `node.py:281`
- [ ] (suggestion) add tests: one-sided table (rx only), empty sectors, out-of-range tx_sector — `kongsberg_em_bridge/test/test_detections.py`
- [ ] (suggestion) out-of-range tx_sector publishes a fabricated tx angle as DETECT_OK; pre-existing, follow-up candidate — `node.py:253`
- [ ] (suggestion) "only datagram types present in a raw capture" cites no capture; name it — `README.md:40`, `node.py:100`
- [ ] (suggestion) plan files row names test_sonar_info.py/test_beamwidth.py; tests live in test_detections.py — `.agent/work-plans/issue-82/plan.md`
- [ ] (suggestion) PR body: bags now carry rejected beams; cube#121 review reasoned from the opposite premise

## Implementation
**Status**: complete (address-findings pass for Local Review (Pre-Push) round 1)
**When**: 2026-09-14 09:35 -04:00
**By**: Claude Code Agent (Claude Fable 5.1)
**Source**: Local Review (Pre-Push) round 1 at `0575194`
**Branch**: feature/issue-82 at `22138a7`

### Resolved
- [x] (must-fix) heartbeat now logs `nvalid` valid of `nrx` beams (all published) — `f9abfbd`
- [x] (must-fix) operator chose: new follow-up marine_tools#85 tracks the M3 figure; both citations repointed, PR closes #82 and #83 — `22138a7`
- [x] (suggestion) README: cube#154 hazard covers any CUBE ingest path incl. offline importers; warning retired with #154 — `72705de`
- [x] (suggestion) cube#154 comment posted: name the three offline tools + "retire the driver warning" acceptance item
- [x] (suggestion) garmin_sidescan README + node.py: cube#30 citation retired → cube#144/PR#153; rviz half-angle → rviz_sonar_image#9 — `72705de`
- [x] (suggestion) `_resolve_beamwidths` treats non-positive table values as unavailable — `f9abfbd`
- [x] (suggestion) tests added: one-sided table, non-positive values, empty sectors, out-of-range tx_sector — `f9abfbd`
- [x] (suggestion) plan files row synced to test_detections.py — `f88c35d`

### Deferred
- [ ] (suggestion) out-of-range tx_sector publishes a fabricated tx angle as DETECT_OK — pre-existing behaviour, now pinned by a test; follow-up candidate, not filed (needs a decision on which flag; DETECT_BAD_FILTER is a stretch)
- [ ] (suggestion) "only datagram types present in a raw capture" — the capture is not named in code or README; the operator knows which `.all` file was read; carried into the PR body as an open question
- [ ] (suggestion) PR body: bags now carry rejected beams (cube#121 review reasoned from the opposite premise)
