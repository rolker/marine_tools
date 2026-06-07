---
issue: 15
---

# Issue #15 — Add garmin_sidescan ROS 2 driver

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-05
**By**: Claude Code Agent (Claude Opus 4.8)
**Verdict**: changes-requested → addressed

**Branch**: feature/issue-15 at `bd646d4`
**Mode**: pre-push
**Depth**: Deep (reason: new safety-critical package — socket I/O, threading, transmit interlock)
**Must-fix**: 5 | **Suggestions**: 4

### Findings
- [x] (must-fix) Failed OFF send recorded as OFF → watchdog self-disables, dry transducer keeps pinging — `node.py:_set_transmit` (fixed: `transmit_state_after`, failed OFF stays transmitting; watchdog retries)
- [x] (must-fix) Watchdog gated on `_transmitting` so one failed stop silenced it — `node.py:_watchdog` (fixed via above; watchdog re-fires while still "on")
- [x] (must-fix) Auto-resume bypassed the transmit guard and had no debounce — `node.py:_on_sound_speed` (fixed: re-checks guard + `_safety_enabled` + sustained `resume_valid_samples`)
- [x] (must-fix) `require_sound_speed:=false` silently disabled the dry-out watchdog — `node.py:_watchdog` (fixed: watchdog decoupled from require_sv, gated on sv_topic; require_sv shown in status)
- [x] (must-fix) Unsynchronized sends / startup OFF outside lock — `node.py` (fixed: `_send_lock` serializes sends, startup OFF under `_tx_lock`)
- [x] (suggestion) rx loop never flushed + unbounded accumulation — `decode.py`/`node.py` (fixed: flush on rx exit + `MAX_SCAN_BYTES` cap)
- [x] (suggestion) shutdown OFF not retried/verified — `node.py:destroy_node` (fixed: retry x3)
- [x] (suggestion) sound-speed QoS mismatch could starve watchdog — `node.py` (verified RELIABLE matches sound_speed_bridge; documented in comment)
- [ ] (suggestion) `dark_layer` rfind can match a signature inside sample bytes — `decode.py:dark_layer` (known limitation; validated on 540 real pings; parse layer length in a follow-up)

### Notes
- Static analysis: ament flake8 + pep257 clean (colcon test).
- Tests: 12 pass (real-data decode, command-frame equivalence, transmit-state safety rule).
- Residual: cross-thread reads of `_safety_latched`/`_last_valid_sv_t` are GIL-atomic single ops; executor callbacks are single-threaded. Acceptable.

## Integrated Review
**Status**: complete
**When**: 2026-06-05
**By**: Claude Code Agent (Claude Opus 4.8)

**PR**: #17 at `39e9bec`
**Sources**: 2 (Copilot R1 @ `39e9bec`, Local Review @ `bd646d4`)
**Cross-source confirmations**: 0 at same head (1 thematic link to Local Review #1)
**CI**: no build/test CI on repo; local colcon test = 14 pass

### Findings
- [ ] (must-fix, Copilot) `_request_transmit` reports success/state regardless of TCP send result — sibling of Local Review #1 at the API boundary, reintroduced by the control-interface refactor — `node.py:_request_transmit`
- [ ] (must-fix, Copilot) missing `setup.cfg` (install_scripts) — entry point may install to bin/, launch_ros may not find executable — `setup.py`/`setup.cfg`
- [ ] (suggestion, Copilot) waterfall `buf += row` → `b''.join(rows)` (cleaner/linear) — `node.py:_publish_waterfalls`

### False positives
- none — all three Copilot findings valid

## Integrated Review
**Status**: complete
**When**: 2026-06-05
**By**: Claude Code Agent (Claude Opus 4.8)

**PR**: #17 at `94cd1a2`
**Sources**: 2 (Copilot R2 @ `94cd1a2`, prior Integrated Review @ `39e9bec`)
**Cross-source confirmations**: theme continued from R1 (ignored send results)
**CI**: no build/test CI on repo; local colcon test gate

### Findings
- [ ] (must-fix, Copilot R2) package.xml missing runtime deps rosidl_runtime_py / launch / launch_ros — `package.xml`
- [ ] (must-fix, Copilot R2) startup range send result ignored, logs success on failure — `node.py:_startup_transmit_state`
- [ ] (must-fix, Copilot R2) range control updates mirror+logs success without checking send — `node.py:_on_control_value`
- [ ] (must-fix, Copilot R2) tvg/interference control updates mirror+logs success without checking send — `node.py:_on_control_value`
- [ ] (must-fix, Copilot R2) range_m param callback reports success ignoring send result — `node.py:_on_param_set`

### False positives
- none — all five valid; same correctness class as R1 _request_transmit, generalized to all remaining send sites

## Integrated Review
**Status**: complete
**When**: 2026-06-05
**By**: Claude Code Agent (Claude Opus 4.8)

**PR**: #17 at `d2c5c78`
**Sources**: Copilot R3 @ `d2c5c78` + prior Integrated Reviews
**Cross-source confirmations**: 0
**CI**: no build/test CI on repo; local colcon test gate

### Findings
- [x] (must-fix, Copilot R3) _rx_loop drops socket on OSError without closing → fd leak — `node.py:_rx_loop` (fixed: `sock.close()` before dropping the reference on the recvfrom OSError path; releases fd + multicast membership on rejoin)
- [x] (must-fix, Copilot R3) range_m param send bypasses range_min..range_max bounds; out-of-range sent + reported success — `node.py:_on_param_set` (fixed: reject out-of-range param set via `range_in_bounds()` with `successful=False` — doesn't silently clamp, so the param store can't hold a value the GCV never got; mirrors the control-set guard)
- [x] (must-fix, Copilot R3) watchdog returns early with no sv_topic; transmitting + require_sv + no SV source never stops — `node.py:_watchdog` (fixed: decision extracted to pure `watchdog_action()`; with no sv_topic it now mirrors `_guard_transmit_on` — stops transmit when `require_sv` is set, leaves it untouched for bench testing with `require_sound_speed:=false`)

### Resolution (fixes applied)
**When**: 2026-06-05 · **By**: Claude Code Agent (Claude Opus 4.8)
- All three R3 must-fixes addressed. Watchdog and range-bounds logic extracted
  to pure predicates (`watchdog_action`, `range_in_bounds`) following the
  existing `transmit_state_after` pattern, with 10 new unit tests in
  `test_safety.py` (no rclpy needed).
- Tests: 24 pass (was 14). ament_flake8 + ament_pep257 clean.

### False positives
- (Copilot R3) launch.py frame_id passed as list "becomes a list not a string" — launch_ros concatenates a substitution list into a single string parameter (documented frame-prefix idiom); production bizzyboat_project11/launch/sound_speed_launch.py:66 uses the identical pattern. rclpy receives a string.

## Integrated Review
**Status**: complete
**When**: 2026-06-07 09:53 -04:00
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))

**PR**: #17 at `d1fb24e`
**Sources**: 1 at head (Copilot R4 @ `d1fb24e`) + prior Integrated Reviews (R1 `39e9bec`, R2 `94cd1a2`, R3 `d2c5c78`)
**Cross-source confirmations**: 0 at `d1fb24e` (no Local Review run against the field-imported head)
**CI**: no build/test CI on repo; local colcon test (ament_flake8/pep257) is the gate — finding 1 fails it

Context: R1–R3 are stale and resolved (confirmed in code + rolker's PR comments).
Head `d1fb24e` is the /import-field-changes fast-forward adding the Marine Network
proxy; Copilot R4 re-reviewed it.

### Findings
- [x] (must-fix, Copilot R4) unused `import struct` fails ament_flake8 (test/test_flake8.py lints tools/) — `garmin_sidescan/tools/garmin_marine_network_proxy.py:39` (fixed: removed import. Re-run surfaced a second latent lint in the field-imported proxy — `Q000` double-quotes at `:192` — also fixed)
- [x] (should-fix, Copilot R4; recurring from stale `5192cab`) `package.xml` missing `rcl_interfaces` exec_depend (node.py:25 imports SetParametersResult) — `garmin_sidescan/package.xml` (fixed: added `<exec_depend>rcl_interfaces</exec_depend>`)
- [x] (should-fix, Copilot R4) `range_m` param: negative/NaN fall through `p.value > 0` guard to successful=True — silently accepted, never applied — `garmin_sidescan/garmin_sidescan/node.py:627` (fixed: branch now entered for any `range_m`; rejects non-finite via `math.isfinite` + out-of-range via `range_in_bounds` with `successful=False`)
- [x] (should-fix, Copilot R4) `_on_param_set` returns successful=True for unhandled names; startup-static params (sv_min/sv_max/auto_resume/channel maps) report success with no effect — reject known-static explicitly, not blanket — `garmin_sidescan/garmin_sidescan/node.py:657` (fixed: declared-but-static params rejected with `successful=False`; `use_sim_time` + undeclared names left to default handling, so rclpy internals aren't rejected)

### False positives
- none this round (prior frame_id-as-list FP from R3 not re-raised at head)

### Resolution (fixes applied)
**When**: 2026-06-07 10:00 -04:00 · **By**: Claude Code Agent (Claude Opus 4.8 (1M context))
- All 4 R4 findings addressed (callback registered after declarations, so
  rejecting `range_m=0.0` at runtime can't break startup; verified at node.py:248).
- `colcon test garmin_sidescan`: **24 tests, 0 failures** (ament_flake8/pep257 green).
- No new unit tests added: `_on_param_set` needs an rclpy Node (the suite is
  deliberately rclpy-free); its validation delegates to `range_in_bounds`
  (already tested in `test_safety.py`) and `math.isfinite`.

## Integrated Review
**Status**: complete
**When**: 2026-06-07 11:15 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))

**PR**: #17 at `bd6e5cb`
**Sources**: Copilot R5 @ `bd6e5cb` (live) + 5 stale Copilot rounds (R1-R4) + 4 prior Integrated Reviews + CI
**Cross-source confirmations**: 0 at head (no Local Review at `bd6e5cb`)
**CI**: copilot-pull-request-reviewer success; no build/test gate on this PR yet (marine_tools#18/PR#19 unmerged)

Context: comments #1-17 (R1-R4, `39e9bec`..`d1fb24e`) all resolved — verified in
code (rcl_interfaces declared, struct import gone) and prior Integrated Reviews;
R5 did not re-raise them. Only R5's two comments are live.

### Findings
- [ ] (HUMAN, rolker conversation) On-boat test data renders wrong in the rqt plugin; find the faulty component across the garmin->proxy->driver->rqt path (test used a separate-machine proxy). Debugging task, not a one-line fix — bisect: raw GCV frames @ proxy vs post-decode vs RawSonarImage fields vs rqt rendering (decode.py orientation/byteorder/scaling; proxy relay; rqt_sonar_waterfall pairing/scaling, PR #41)
- [ ] (safety, Copilot R5) `transmit_state_after(True, send_ok=False)` forces `_transmitting=False`: failed safety OFF (stays True) -> SV recovers via ROS topic while control-TCP down -> auto-resume ON send also fails -> watchdog disarms while a dry transducer may still ping — `node.py:322` (fix: on failed ON keep prior `_transmitting`, so failed-ON-after-failed-OFF stays True; add unit test for that sequence)
- [ ] (should-fix, Copilot R5) `_on_param_set` applies side effects (range `_send`+mirror+publish) during iteration then returns `successful=False` later in the same batch on a startup-static param -> bundled `set_parameters([range_m,sv_min])` commands GCV + updates UI but rclpy rejects the batch -> param store desync — `node.py:626` (fix: validate-all-then-apply)

### False positives
- none this round (R3 frame_id-as-list FP not re-raised at head)
