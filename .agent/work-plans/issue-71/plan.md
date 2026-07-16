# Plan: kongsberg_em_bridge publishes the angular-response curve in SonarInfo

## Issue

https://github.com/rolker/marine_tools/issues/71

## Context

Curve-delivery arc (decided 2026-07-16): the driver declares the sensor's
empirical angular-response calibration in `SonarInfo` — the CameraInfo model —
so it is recorded in bags beside the data it corrects, and CUBE consumers
(rolker/cube_bathymetry#102) read it from there with auto-enable. Wire-format
prerequisite rolker/unh_marine_autonomy#268 (TL provenance fields) is on PR
rolker/unh_marine_autonomy#269.

Carried from the #268 review: the producer must set
`angular_response_absorption_db_per_m = NaN` explicitly even when publishing
no curve (rosidl float default 0.0 violates the message's sentinel
convention).

## Approach

1. **`kongsberg_em_bridge/angular_response.py`** (new, pure/framework-free
   like `em_datagrams.py`): `load_angular_response_curve(path)` returning
   `(points, tl_removed, absorption_db_per_m)`, mirroring
   `cube::loadAngularResponseCurveWithHeader` exactly:
   - empty path / missing / unreadable file → `([], False, 0.0)`;
   - `#` comments mined for `tl_removed` (case-insensitive `true`/`1`) and
     `absorption_db_per_m` (malformed value → tier-1 default kept);
   - data rows: split on commas, ≥4 fields, columns 0 and 3, parsed with
     `std::stof` semantics (leading numeric prefix accepted, e.g. `1deg` →
     1.0; header row rejected because it has no numeric prefix);
   - sorted ascending by angle.
2. **`node.py`**:
   - New parameter `angular_response_curve_file` (default '' = fields stay
     empty/unknown). Loaded once at startup — the curve is calibration, not
     an operator setting; warn when a configured path yields an empty curve.
   - `sonar_info_from_parsed` gains an optional `angular_response` argument;
     fills the curve arrays plus provenance: no curve → `TL_UNKNOWN` +
     explicit NaN absorption; curve with `tl_removed` → `TL_REMOVED` +
     loaded absorption verbatim (the consumer never recomputes alpha);
     curve without → `TL_IN` + NaN.
3. **`package.xml` / README**: parameter documented; no new deps
   (marine_interfaces already a depend).
4. **Tests**: parser (tier-1/tier-2 round-trip, header-comment variants,
   malformed rows/values, missing file, unsorted input, stof-prefix
   tolerance) + SonarInfo population for all three provenance cases +
   explicit-NaN-with-empty-curve.

## Ordering

Builds against the #268 fields — buildable once
rolker/unh_marine_autonomy#269 merges and the main core layer is rebuilt.

## Verification

- Package tests green; end-to-end smoke (existing scratchpad harness
  extended): node with a tier-2 curve file publishes SonarInfo whose curve +
  provenance round-trip over the wire.
