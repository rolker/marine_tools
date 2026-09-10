# Plan: M3 and DeltaT drivers publish no beamwidths — follow the garmin_sidescan precedent, after the consumer unit fix

## Issue

https://github.com/rolker/marine_tools/issues/82

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

## Approach

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

2. **Wire the resolver into `_publish`** (`node.py:537-551`), replacing the
   stale comment and the always-empty fields:
   ```python
   rx_bw, tx_bw = _resolve_beamwidths(parsed['model'])
   if rx_bw is not None:
       info.rx_beamwidths = [rx_bw] * len(parsed['beams'])  # see note below
   if tx_bw is not None:
       info.tx_beamwidths = [tx_bw] * len(parsed['beams'])
   ```
   Confirm during implementation whether `PingInfo.rx_beamwidths` /
   `tx_beamwidths` are per-beam arrays (matching `beams`) or per-sector
   arrays (matching `sectors`, like `pulse_lengths` in `SonarInfo`) by
   reading the installed `marine_acoustic_msgs/msg/PingInfo.msg` directly
   (not assumed) — `garmin_sidescan` publishes a single-element list per
   ping (`[rx_bw]`) because it has one beam per message; the M3 publishes
   many beams per message, so the array shape must match what
   `cube_bathymetry`'s `per_beam_beamwidth_usable` consumer actually
   iterates (`error_model.h` — read the per-beam loop to confirm length
   expectations before finalizing this line). This is an implementation
   detail to nail down against source, not a design choice to guess at
   here.

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

## Files to Change

| File | Change |
|------|--------|
| `kongsberg_em_bridge/kongsberg_em_bridge/node.py` | Add `_RX_BEAMWIDTH_RAD`/`_TX_BEAMWIDTH_RAD` (or combined) table keyed by `.all` model number, `_resolve_beamwidths()` pure helper, wire into `_publish()`, rewrite the stale `node.py:544-548` comment |
| `kongsberg_em_bridge/test/test_sonar_info.py` or a new `test_beamwidth.py` | Unit tests for `_resolve_beamwidths` (M3 uncharacterised, unknown model) and the empty-fields-on-publish behaviour |
| `kongsberg_em_bridge/README.md` | New beamwidth/sensor-constants section mirroring `garmin_sidescan/README.md:140-176`; note the M3 is currently uncharacterised |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Never document from assumptions | Beamwidth figures are left empty rather than estimated; the PR only documents the *mechanism* (table, resolver, empty convention), never a guessed number. `PingInfo.msg` array-shape (per-beam vs per-sector) is confirmed against the installed message definition before finalizing step 2, not assumed from the `garmin_sidescan` single-beam case. |
| A change includes its consequences | `kongsberg_em_bridge/README.md` gets the same beamwidth documentation `garmin_sidescan/README.md` already has (Documentation & Instruction Impact below) |
| Test what breaks | Explicit test for the uncharacterised-device-leaves-fields-empty behaviour (the issue's own Acceptance item), plus unknown-model-number coverage |
| Only what's needed | Scoped to the M3 half only; DeltaT split to its own repo/issue per operator decision; no beamwidth figure invented to "complete" the feature |
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

## Documentation & Instruction Impact

- **Stale docs** (must land in this PR): `kongsberg_em_bridge/kongsberg_em_bridge/node.py:544-548` (the "avoids a unit mismatch" / `cube_bathymetry#30` comment — factually stale now that #153 is merged) and `kongsberg_em_bridge/README.md` (missing beamwidth section, now needed since the driver's `PingInfo` output behavior changes from "always empty" to "table-resolved, empty only when uncharacterised").
- **Agent-instruction candidates** (proposals only — operator decides): None. This PR follows an existing, already-documented precedent (`garmin_sidescan`'s empty-when-uncharacterised convention) rather than establishing a new pattern; no new `.agent/knowledge/` entry is proposed.

## Open Questions

- None blocking implementation. The one substantive open question the Issue
  Review raised — whether a sourced M3 datasheet beamwidth figure exists —
  is resolved by operator decision: ship uncharacterised (empty), do not
  guess. If a datasheet figure surfaces later, populating it is a follow-up
  PR (fill in `_RX_BEAMWIDTH_RAD[30]`/`_TX_BEAMWIDTH_RAD[30]`, add the
  table-coverage test case, update the README table), not part of this one.
- Implementation must confirm `PingInfo.rx_beamwidths`/`tx_beamwidths` array
  cardinality (per-beam vs per-sector) against the installed
  `marine_acoustic_msgs/msg/PingInfo.msg` before finalizing the `_publish()`
  wiring (step 2) — flagged there as a verify-before-code step, not a design
  decision needing operator input.

## Estimated Scope

Single PR, single repo (`marine_tools`, `kongsberg_em_bridge` package only).
