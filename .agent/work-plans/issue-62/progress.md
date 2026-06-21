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

## Implementation
**Status**: complete
**When**: 2026-06-21 05:00 +00:00
**By**: Claude Code Agent (Claude Opus)

**Branch**: feature/issue-62 at `c3dcec5`
**Commit**: `c3dcec5` — garmin_sidescan: fill ping_info frequency + beamwidths from generation table (#62)

### What changed
- `garmin_sidescan/garmin_sidescan/node.py`:
  - Added module-level `_FREQ_HZ` (gen, side → Hz; GCV-20 SideVü 1.12 MHz /
    ClearVü 820 kHz, GCV-10 455 / 800 kHz) and the **settled** beamwidth tables
    `_RX_BEAMWIDTH_RAD` (across-track wide fan: 55°/55°/46°) and
    `_TX_BEAMWIDTH_RAD` (along-track narrow: 0.44°/0.44°/0.74°), full −3 dB
    widths in radians per `PingInfo.msg`. GCV-10 beamwidths omitted (unconfirmed).
  - Added pure, ROS-free helper `_resolve_freq_bw(gen, side, freq_override)` →
    `(freq, rx_or_None, tx_or_None)`: non-zero param overrides the freq table;
    unknown generation → freq 0.0 and both beamwidths `None`.
  - `_make_sonar_msg` now computes the effective generation (explicit `device`
    param wins over the auto-detect vote) and stamps `ping_info.frequency`,
    `rx_beamwidths`, `tx_beamwidths` via the helper. Updated the `freq_*_hz`
    param comment to mention the table fallback.
- `garmin_sidescan/README.md`: rewrote the self-contradicting "Protocol notes"
  frequency paragraph (it documented the *opposite* decision — review-plan
  must-fix #1) and added a "Sensor constants" subsection (frequency table +
  marketing-label note, rx=across / tx=along axis convention in radians, GCV-10
  omission, and a pointer that CUBE's degrees read (cube#30) and rviz's
  half-angle read are consumer bugs handled elsewhere). Updated the `freq_*_hz`
  Key-parameters row.
- `garmin_sidescan/test/test_node.py` (new): table coverage (all freq + the 3
  rx + 3 tx entries present/positive/sane, rx > tx, rx < π, GCV-10 absent),
  table-fills-when-param-zero (gcv20 port → 1.12e6 / rad(55) / rad(0.44); down
  channel too), explicit-param-overrides-table, and unknown-generation →
  freq 0.0 + both beamwidths empty. Tests hit the pure helper (no ROS spin).

### Build / test status
- **Lint**: `ament_flake8` and `ament_pep257` both pass on `node.py` and the new
  `test/test_node.py` (exit 0).
- **Helper logic**: verified standalone (extracted the tables + `_resolve_freq_bw`
  via AST, ran every test assertion — all pass) since it is deliberately ROS-free.
- **Field names**: confirmed against `/opt/ros/jazzy/share/marine_acoustic_msgs/
  msg/PingInfo.msg` — `frequency`, `tx_beamwidths`, `rx_beamwidths`, documented
  there as "-3db beamwidths" (radians), matching the convention used here.
- **`colcon test` NOT run in-container**: importing `node.py` pulls in two
  lower-layer deps that are not built in this worktree's install spaces —
  `marine_control_py` (core_ws, a shared lower layer) and
  `marine_radar_control_msgs` (needs the rosidl toolchain). `marine_acoustic_msgs`
  and `rcl_interfaces` are present. Building the lower layers is a workspace-
  provisioning step (and would write into the shared `main/core_ws/install`), not
  part of this code change. **Host must run `colcon test` for `garmin_sidescan`
  with the layers built to confirm the four pytest cases pass under a real spin.**

### Notes for review
- The beamwidth convention follows the SETTLED operator decision (plan commit
  `10a175e`): tx=along-track, rx=across-track, full −3 dB radians. The stale
  "Step 4" text in `plan.md` (still naming `_BEAMWIDTH_RAD` / a 27.5° half-angle)
  was superseded by the settled tables at `plan.md:46-63` and was not followed.
- Review-plan must-fix #2 (the cube/kongsberg `rx_beamwidths` semantics dispute)
  is, per the dispatch instructions, treated as a **consumer-side** concern fixed
  in cube_bathymetry (cube#30) / rviz_sonar_image — deliberately NOT touched here.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-21 05:27 +00:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: changes-requested

**Branch**: feature/issue-62 at `68254a4`
**Mode**: pre-push
**Depth**: Standard (reason: cross-package message-interface change — rx/tx_beamwidths semantics, flagged must-fix at plan review)
**Must-fix**: 1 | **Suggestions**: 5
**Round**: 1 | **Ship**: continue — producer code is spec-correct, but one cross-package rollout consequence (CUBE/rviz misread) needs verification before push, plus minor doc/test items

### Findings
- [ ] (must-fix) Populating `rx_beamwidths`/`tx_beamwidths` changes output for consumers that currently misread it — CUBE reads it as degrees (cube_bathymetry#30), rviz as a half-angle. Code is correct per `PingInfo.msg`; verify those consumer fixes are merged (gh unauthenticated here, unconfirmed) or coordinate deployment ordering before pushing, else CUBE footprint/error is corrupted (kongsberg leaves it empty for this reason). Residue of plan-review must-fix #2. — `garmin_sidescan/garmin_sidescan/node.py:855-858`
- [ ] (suggestion) README arithmetic contradiction: band "1,060–1,170 kHz" with "band centre 1,120 kHz", but the midpoint is 1,115 kHz — reword for self-consistency. — `garmin_sidescan/README.md` (Sensor constants)
- [ ] (suggestion) No test for the partial-coverage branch (`gcv10` → real freq but `rx`/`tx` None); add `_resolve_freq_bw('gcv10','down',0.0) == (800000.0, None, None)`. — `garmin_sidescan/test/test_node.py`
- [ ] (suggestion) Override asymmetry: freq has a `freq_*_hz` escape hatch, beamwidth has none; consider a `beamwidth_*_rad` override / enable flag. — `garmin_sidescan/garmin_sidescan/node.py:88-110,240-242`
- [ ] (suggestion) rx=across / tx=along is a producer convention not derivable from `PingInfo.msg`; add a one-line caveat for averaging/comparing consumers. — `garmin_sidescan/README.md` (beamwidth section)
- [ ] (suggestion) Negative `freq_*_hz` passes the `==0.0` override gate and publishes verbatim (pre-existing); a `>=0` guard / `FloatingPointRange(from_value=0.0)` would close it. — `garmin_sidescan/garmin_sidescan/node.py:134`

### Notes
- Static analysis clean under the authoritative ament profile (`ament_flake8` + `ament_pep257`, all three changed `.py` files, no problems). Plain `flake8` import-order/docstring plugins (I101/I201/D1xx) are not in the ament config and mostly hit untouched pre-existing import lines — not applicable.
- `colcon test` still not run in-container (lower-layer deps `marine_control_py` / `marine_radar_control_msgs` unbuilt — see Implementation entry); helper logic re-verified standalone. Host should run the four `test_node.py` cases under a real spin.
- Reviewed against `origin/jazzy` (8 commits ahead, clean merge-base); the repo symref default `noetic` is the ROS1 line and not the base for this ROS2 work.
