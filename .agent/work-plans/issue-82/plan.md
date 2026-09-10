# Plan: M3 beamwidth table (#82) + publish invalid beams with honest flags (#83)

## Issues

- https://github.com/rolker/marine_tools/issues/82 — M3 and DeltaT drivers
  publish no beamwidths; follow the `garmin_sidescan` precedent.
- https://github.com/rolker/marine_tools/issues/83 — the driver drops beams
  the sonar flagged invalid, so the flag field is decorative and the bag
  loses the fact.

Both land on one branch (`feature/issue-82`) as two atomic commits and one PR
closing both: they touch the same ~40 lines of `_publish` in the same package,
and splitting them would mean one of the two rebasing onto the other for no
review benefit.

## Scope (operator decision — see Issue Review + host instructions)

This PR covers the **Kongsberg M3 (`kongsberg_em_bridge`) half only**. The
DeltaT half is filed separately as
[rolker/imagenex_deltat#1](https://github.com/rolker/imagenex_deltat/issues/1)
— the DeltaT driver lives in its own repo and cannot be touched from a
marine_tools PR; its live driver publishes only a point cloud and emits no
`SonarDetections` at all, so there is nowhere for beamwidth data to land there
yet. Issue #82's Acceptance criteria for the DeltaT ("DeltaT likewise") are
met by that split, not by this PR.

**No M3 beamwidth figure is populated.** No sourced M3 beamwidth figure exists
anywhere in the workspace — the only number in the whole chain is a generic
uncited 2° default in `cube_bathymetry`'s `Device::across_track_beamwidth` /
`along_track_beamwidth` fallback (`error_model.h:38,41`). Per operator
direction, the deliverable ships the table/resolution machinery with the M3
entry **uncharacterised (empty)**, exactly like `garmin_sidescan` already does
for the GCV-10. A wrong beamwidth stamped confidently is worse than an absent
one — that is issue #82's own rule, and it is what makes the "uncharacterised
device leaves fields empty, with a test" Acceptance item achievable today. Do
not invent, estimate, or derive a figure. Figures get filled in as datasheets
turn up (separate, future PRs).

## Ordering gate — verified satisfied

The issue's own gate ("do not populate first") is `cube_bathymetry#144`,
fixed via PR [rolker/cube_bathymetry#153](https://github.com/rolker/cube_bathymetry/pull/153),
merged **2026-09-10** as commit `54e891f`. Verified directly against the
merged source, not taken on faith:

- `cube_bathymetry/cube_bathymetry/include/cube_bathymetry/error_model.h:241-247`:
  `tx_beamwidths` / `rx_beamwidths` are documented "Sonar reported -3db
  transmit/RECEIVE beamwidths in radians"; the stale "said transmit" unit
  confusion is called out and fixed in the comment itself.
- `error_model.h:266,279,285-293`: per-beam values are validated via
  `ErrorModel::per_beam_beamwidth_usable(float beamwidth_rad)` — finite,
  strictly positive, below a largest-acceptable-radians bound — falling back
  to the device beamwidth otherwise, rather than trusting an unvalidated
  producer value.
- `error_model.h:399-406`: `Device::across_track_beamwidth` (degrees, as
  authored by a human) is converted to radians **once**, at construction,
  into `device_across_track_beamwidth_rad_` — the single boundary
  conversion — so every other use site, including the per-beam path, reads
  radians throughout with no second, hidden `pi/180`.

This means populating `rx_beamwidths`/`tx_beamwidths` in radians today is
safe: the 57x-error failure mode the issue describes (populating radians
against a consumer that still multiplied by `pi/180`) no longer exists.

## Ordering gate for #83 — real, and deliberately overridden by the operator

`#83`'s own gate is
[rolker/cube_bathymetry#154](https://github.com/rolker/cube_bathymetry/issues/154):
the CUBE error model does not read `detection_flags`, so an invalid beam's
zero travel time becomes a sounding at zero depth — seafloor at the surface.
That gate is real and `#154` is **not** landed.

**The operator has decided to ship the driver change first anyway**
(2026-09-10). His reasoning, recorded as given: there is no near-term plan to
collect M3 data, and if any is collected before the consumer is fixed, the
resulting bad data is what will motivate fixing it.

He also directed (2026-09-10) that the `skip_invalid_beams` parameter be
**removed outright** rather than kept as an escape hatch, in his own words:

> "Remove it, I need to keep things as lean and as clean as practicle. Also,
> it's a bug that a consumer doesn't respect the flag so we shouldn't be
> working around bugs we can fix ourselves."

So: no parameter, no revert path, and the consumer's flag-blindness is named
as a bug we own (`cube_bathymetry#154`) rather than a constraint the driver
designs around. This plan does not add a second gate of its own and does not
hedge the change. What it does instead is make the state legible where
someone will actually meet it — an unconditional startup warning naming
`#154`, and an honest note in the README.

## Context

`kongsberg_em_bridge/kongsberg_em_bridge/node.py:537-551` (`_publish`) builds
a `PingInfo` per ping and leaves `tx_beamwidths`/`rx_beamwidths` unset, with a
comment (`node.py:544-548`) explaining why — citing the closed umbrella issue
`cube_bathymetry#30` rather than the actual fix, and asserting a unit-mismatch
rationale that `#153` has now made false. The M3 datagram stream carries no
beamwidth data itself (verified against raw `.all` captures — no
runtime-parameters datagram is present), so this must come from a device
table, not the wire, matching the issue's own finding.

`garmin_sidescan` already solved this shape for its own sensor
(`garmin_sidescan/node.py:88-135` tables + `_resolve_freq_bw`, `:851-858`
publish site) and documents it (`garmin_sidescan/README.md:140-176`) and
tests it (`garmin_sidescan/test/test_node.py`: table-coverage,
unknown-generation, partial-coverage cases). `kongsberg_em_bridge` publishes a
single sensor family (Kongsberg `.all`, `sonar_model_name()` already maps
model number 30 → `'kongsberg-m3'`, anything else → `kongsberg-em<model>`),
so the table only needs one axis — model number, not `(generation, side)` —
but the "empty when uncharacterised" resolution shape is identical.

## Approach — commit 1 (#82, beamwidths)

1. **Add a beamwidth table + resolver in `kongsberg_em_bridge/node.py`,
   next to `sonar_model_name`.**
   - `_RX_BEAMWIDTH_RAD = {30: None}` / `_TX_BEAMWIDTH_RAD = {30: None}` (or a
     single `_BEAMWIDTH_RAD = {30: (rx, tx)}` dict) keyed by the `.all` model
     number, mirroring `sonar_model_name`'s existing key. Model 30 (M3) maps
     to `None` for both axes — uncharacterised, no datasheet figure exists.
     Any other model number (not currently seen in practice) also resolves to
     `None` via `.get(model)` returning `None` for missing keys, matching
     `garmin_sidescan`'s unknown-generation behaviour.
   - `_resolve_beamwidths(model) -> (rx_rad_or_None, tx_rad_or_None)`: a pure
     module-level function (no `self`), unit-testable without an rclpy node,
     matching `_resolve_freq_bw`'s shape.
   - Docstring/comment states plainly: values are full -3 dB widths in
     radians per `PingInfo.msg`; `None` means "not characterised for this
     model", the field is left empty, and the CUBE error model's documented
     fallback (`Device::across_track/along_track_beamwidth`) applies — this
     is deliberate, not a gap.

2. **Wire the resolver into the publish path** (`node.py:537-580`),
   replacing the stale comment and the always-empty fields.

   Cardinality is **resolved fact**, not an open question (plan review):
   `cube_bathymetry/error_model.cpp:277` indexes
   `detections.ping_info.rx_beamwidths[i]` with the same per-beam `i` it uses
   for `two_way_travel_times[i]`/`flags[i]`, and `ros2sonic`'s converter
   `resize(num_beams)` independently confirms the per-beam convention. So the
   beamwidth arrays are **per published beam**.

   Per the plan review's must-fix, they are therefore built by **appending
   inside the existing publish loop**, exactly like every sibling per-beam
   array — never sized from `len(parsed['beams'])`, the raw pre-filter count.
   That keeps them the same length as `flags`/`two_way_travel_times`/... by
   construction rather than by coincidence.

   Only the table/resolver *shape* transfers from `garmin_sidescan`: it
   publishes `RawSonarImage`, one beam per message, hence its literal
   singleton `[rx_bw]` at `garmin_sidescan/node.py:855-858`. This driver
   publishes `SonarDetections` with many beams per message, so the publish
   site needs its own append-in-loop pattern, not a copy of that line.

   To make the publish path testable without an rclpy node — and to match
   the module's existing `sonar_info_from_parsed` pure-builder pattern — the
   message construction moves out of the `_publish` method into a
   module-level `detections_from_parsed(parsed, frame_id, stamp)`. `_publish`
   keeps the node-side concerns (stamp, SonarInfo change detection,
   publishing, the health heartbeat).

3. **Rewrite the stale comment at `node.py:544-548`.** Replace the
   "avoids a unit mismatch" claim (no longer true) and the `cube_bathymetry#30`
   citation (stale umbrella issue) with a comment that:
   - States the M3 beamwidth is not characterised (no datasheet figure on
     hand) and the fields are therefore left empty, deliberately, per the
     `garmin_sidescan` precedent.
   - Cites `cube_bathymetry#144` / PR
     [rolker/cube_bathymetry#153](https://github.com/rolker/cube_bathymetry/pull/153)
     as the reason it is now *safe* to populate values here once a figure
     exists — the consumer reads radians correctly and validates per-beam
     values, so a future figure can be added without the historical
     unit-mismatch risk.

4. **Tests** (`kongsberg_em_bridge/test/`, following
   `garmin_sidescan/test/test_node.py`'s shape and
   `kongsberg_em_bridge/test/test_sonar_info.py`'s existing pure-function
   test pattern):
   - `test_resolve_beamwidths_m3_uncharacterised`: model 30 → `(None, None)`.
   - `test_resolve_beamwidths_unknown_model`: an unmapped model number (e.g.
     2040) → `(None, None)`, matching the "no false confidence for an unseen
     model" behaviour.
   - `test_publish_leaves_beamwidths_empty_for_uncharacterised_device`: an
     end-to-end check (construct a minimal `parsed` dict as
     `test_sonar_info.py`'s `_parsed()` helper does, call `_publish`-adjacent
     logic or the resolver + field-assignment directly) proving
     `msg.ping_info.rx_beamwidths` / `tx_beamwidths` are empty lists for the
     M3 today — this is the issue's explicit "an uncharacterised device
     leaves the fields empty, with a test" Acceptance item.
   - If a `(rx, tx)` pair is later added for a hypothetical characterised
     model in a table-coverage test (mirroring
     `test_freq_beamwidth_table_coverage`), assert the array is populated
     with the exact radians value and is *not* re-derived from degrees.

5. **Documentation**: add a "Sensor constants" / beamwidth section to
   `kongsberg_em_bridge/README.md`, mirroring
   `garmin_sidescan/README.md:140-176` (`## Topics` already documents
   `PingInfo`'s role generally; add a dedicated subsection). State: table is
   keyed by `.all` model number, values are full -3 dB widths in radians per
   `PingInfo.msg`, the M3 (model 30) is currently uncharacterised (no cited
   datasheet figure) and therefore left empty, and empty fields make the
   CUBE error model fall back to its own generic `Device` beamwidth
   (`cube_bathymetry`'s `error_model.h`) rather than being silently
   zero-filled. Cross-reference `cube_bathymetry#144`/PR#153 the same way the
   node.py comment does.

## Approach — commit 2 (#83, publish invalid beams)

6. **Delete `skip_invalid_beams` entirely** — the parameter declaration
   (`node.py:183-186`), the `self.skip_invalid` member (`node.py:227`), and
   the `if self.skip_invalid and not beam['valid']: continue` branch in the
   publish loop. The driver always publishes every beam the sonar reported,
   each carrying its honest `DetectionFlag` (`DETECT_OK` /
   `DETECT_BAD_SONAR`). Operator direction, above: keep it lean, and don't
   work around a consumer bug we can fix ourselves.

7. **Comment at the old parameter's site**: state plainly that the driver
   reports what the sonar reported, that filtering is the consumer's job, and
   that `cube_bathymetry#154` tracks the consumer that does not yet do it.

8. **Unconditional startup warning** (warning level, once, in `__init__`):
   name `cube_bathymetry#154` and say that a consumer which ignores detection
   flags will treat an invalid beam's zero travel time as a sounding at zero
   depth. With the parameter gone this is the only remaining in-band signal,
   so whoever meets strange data in the field finds the pointer in the log
   rather than in an issue tracker they were not reading.

9. **Tests**: an invalid beam is published carrying `DETECT_BAD_SONAR` rather
   than vanishing; and every per-beam array in the message
   (`flags`, `two_way_travel_times`, `tx_delays`, `intensities`, `tx_angles`,
   `rx_angles`, and the beamwidth arrays when populated) stays the same
   length. There is now only one configuration to pin, but the alignment
   invariant is still worth pinning — the arrays are built in one loop and
   must not diverge.

10. **README**: drop the `skip_invalid_beams` row from the parameter table
    and state in the topic/behaviour text that invalid beams are published
    with `DETECT_BAD_SONAR`, that the consumer is **not** yet fixed
    (`cube_bathymetry#154`), and that a flag-blind consumer will read an
    invalid beam's zero travel time as a zero-depth sounding.

## Files to Change

| File | Change |
|------|--------|
| `kongsberg_em_bridge/kongsberg_em_bridge/node.py` | Add `_RX_BEAMWIDTH_RAD`/`_TX_BEAMWIDTH_RAD` (or combined) table keyed by `.all` model number, `_resolve_beamwidths()` pure helper, wire into `_publish()`, rewrite the stale `node.py:544-548` comment |
| `kongsberg_em_bridge/test/test_sonar_info.py` or a new `test_beamwidth.py` | Unit tests for `_resolve_beamwidths` (M3 uncharacterised, unknown model) and the empty-fields-on-publish behaviour |
| `kongsberg_em_bridge/README.md` | New beamwidth/sensor-constants section mirroring `garmin_sidescan/README.md:140-176`; note the M3 is currently uncharacterised. Commit 2: remove the `skip_invalid_beams` parameter row, document that invalid beams are published with `DETECT_BAD_SONAR` and that `cube_bathymetry#154` is still open |
| `kongsberg_em_bridge/kongsberg_em_bridge/node.py` (commit 2) | Remove the `skip_invalid_beams` parameter, `self.skip_invalid`, and the skip branch; comment at the old declaration site; unconditional startup warning naming `cube_bathymetry#154` |
| `kongsberg_em_bridge/test/test_detections.py` (commit 2) | Invalid beam published with `DETECT_BAD_SONAR`; per-beam array alignment |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Never document from assumptions | Beamwidth figures are left empty rather than estimated; the PR only documents the *mechanism* (table, resolver, empty convention), never a guessed number. `PingInfo.msg` array-shape (per-beam vs per-sector) is confirmed against the installed message definition before finalizing step 2, not assumed from the `garmin_sidescan` single-beam case. |
| A change includes its consequences | `kongsberg_em_bridge/README.md` gets the same beamwidth documentation `garmin_sidescan/README.md` already has (Documentation & Instruction Impact below) |
| Test what breaks | Explicit test for the uncharacterised-device-leaves-fields-empty behaviour (the issue's own Acceptance item), plus unknown-model-number coverage |
| Only what's needed | Scoped to the M3 half only; DeltaT split to its own repo/issue per operator decision; no beamwidth figure invented to "complete" the feature |
| Remove obsolete features outright, not opt-in | `skip_invalid_beams` is deleted, not defaulted off — operator direction, and the workspace's stated preference |
| Enforcement over documentation | The per-beam array-alignment invariant is pinned by a test rather than asserted in a comment |
| Capture decisions, not just implementations | The rewritten `node.py` comment records *why* the fields are still empty (uncharacterised, not a unit-mismatch workaround) and cites the issue that makes it safe to fill in later |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| 0008 — ROS 2 conventions | No | No new packages, topics, services, or interface changes — same `PingInfo` fields, same topic |
| 0003 — Project-agnostic workspace | N/A | Target is a project repo (`marine_tools`), not the workspace repo |
| (all others) | No | No workspace-repo, CI, or branch-protection changes |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| `node.py`'s beamwidth handling (was: always empty) | `kongsberg_em_bridge/README.md` (documents `PingInfo` content) | Yes — step 5 |
| The stale `cube_bathymetry#30` citation in a code comment | Nothing else cites it in this repo (grep confirmed only this one comment) | Yes — step 3 |
| Test coverage for `node.py`'s pure helpers | `test/test_sonar_info.py` already covers `sonar_model_name`/`sonar_info_from_parsed`; new tests extend the same file or a sibling, no new test infra needed | Yes — step 4 |
| Removing the `skip_invalid_beams` parameter | `kongsberg_em_bridge/README.md` parameter table; the `node.py:183-185` comment describing the old rationale; any launch file or config setting it (grep before removing) | Yes — steps 6, 7, 10 |
| The driver now emits invalid beams | The flag-blind consumer (`cube_bathymetry#154`) will read them as zero-depth soundings — surfaced by the unconditional startup warning and the README note; the fix itself belongs to `cube_bathymetry`, not this PR | Yes — steps 8, 10 |

## Documentation & Instruction Impact

- **Stale docs** (must land in this PR): the `node.py:183-185` `skip_invalid_beams` comment (describes a rationale the parameter's removal retires) and the README's parameter row for it; `kongsberg_em_bridge/kongsberg_em_bridge/node.py:544-548` (the "avoids a unit mismatch" / `cube_bathymetry#30` comment — factually stale now that #153 is merged) and `kongsberg_em_bridge/README.md` (missing beamwidth section, now needed since the driver's `PingInfo` output behavior changes from "always empty" to "table-resolved, empty only when uncharacterised").
- **Agent-instruction candidates** (proposals only — operator decides): None. This PR follows an existing, already-documented precedent (`garmin_sidescan`'s empty-when-uncharacterised convention) rather than establishing a new pattern; no new `.agent/knowledge/` entry is proposed.

## Open Questions

- None blocking implementation. The one substantive open question the Issue
  Review raised — whether a sourced M3 datasheet beamwidth figure exists —
  is resolved by operator decision: ship uncharacterised (empty), do not
  guess. If a datasheet figure surfaces later, populating it is a follow-up
  PR (fill in `_RX_BEAMWIDTH_RAD[30]`/`_TX_BEAMWIDTH_RAD[30]`, add the
  table-coverage test case, update the README table), not part of this one.
- Cardinality is **resolved** (see Approach step 2): the beamwidth arrays are
  per published beam, confirmed against `cube_bathymetry/error_model.cpp:277`
  and `ros2sonic`'s converter. No longer an open question.
- The `#83` ordering gate (`cube_bathymetry#154`) is open and knowingly
  overridden by the operator — a recorded decision, not an unresolved
  question. See the ordering-gate section above.

## Estimated Scope

Single PR, single repo (`marine_tools`, `kongsberg_em_bridge` package only),
two atomic commits, closing both #82 and #83.
