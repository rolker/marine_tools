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
- [x] (safety, Copilot R5) `transmit_state_after(True, send_ok=False)` forces `_transmitting=False`: failed safety OFF (stays True) -> SV recovers via ROS topic while control-TCP down -> auto-resume ON send also fails -> watchdog disarms while a dry transducer may still ping — `node.py:322` (fixed in `6335f57`: `transmit_state_after` now takes `prior` and keeps it on a failed ON; failed-ON-from-off still stays OFF; unit test `test_failed_on_while_possibly_pinging_stays_transmitting`)
- [x] (should-fix, Copilot R5) `_on_param_set` applies side effects (range `_send`+mirror+publish) during iteration then returns `successful=False` later in the same batch on a startup-static param -> bundled `set_parameters([range_m,sv_min])` commands GCV + updates UI but rclpy rejects the batch -> param store desync — `node.py:626` (fixed in `6335f57`: validate-all-then-apply; fallible range send runs first so it mutates no local state on failure)

### False positives
- none this round (R3 frame_id-as-list FP not re-raised at head)

### Resolution (fixes applied)
- Both R5 code findings fixed in `6335f57`; `colcon test garmin_sidescan` = 29 tests, 0 failures (flake8/pep257 green).
- Added `_on_param_set` unit tests via a lightweight fake-node (calling the unbound method against a stub `self`) — supersedes the R4 note that the rclpy-free suite couldn't cover it: batch-rejects-static-without-sending-range, order-independent, send-failure-doesn't-mirror.
- C1 (human: on-boat data looks wrong in rqt) — offline investigation done; see below.

### C1 investigation: on-boat data renders wrong in rqt (via separate-machine proxy)
Ruled OUT (offline, against the real-capture fixture `test/fixtures/gcv_real_pings.bin` + code read):
- **Sample bit depth.** Tested the hypothesis that dark-layer samples are 16-bit LE (the layer-header sig `ae 02 ac 02` looked like LE pairs). Refuted empirically: even/odd byte means are identical (72.4/72.8), adjacent bytes are *more* correlated than every-other (13.2 < 20.0 — opposite of a 16-bit interleave), and a scan line reads as saturated nadir (255s) decaying with range. Data is genuinely 8-bit; the driver's `dtype=UINT8` is correct.
- **Proxy mangling datagrams.** `garmin_marine_network_proxy.py` imagery relay is 1:1 `recvfrom(65535)`→`sendto(payload)` — datagram boundaries preserved; it doesn't coalesce/split.
- **rqt decode/orientation.** `decode_samples` handles UINT8; `combine_rows` reverses port so nadir sits at center (standard sidescan). Correct on clean input — the known-good fixture reassembles to clean 2048-bin lines.
- **RX behind proxy.** driver binds `('',50220)` (INADDR_ANY) + joins mcast; receives the proxy's relayed unicast — consistent with data being produced.

DECISIVE EVIDENCE (the bag `~/data/logs/bizzy_sidescan/bag_2026-06-05T14.03.53_sidescan`
+ the day's GCV-20 pcap `~/garmin_sidescan/captures/gcv20_passive_*.pcap`):
- Recorded `sonar_image_port`: 398/400 pings are exactly 2076 B (only 2 short),
  dtype=UINT8, mean **214**, **98% of samples >127**, FLAT profile (no range
  decay), clearvu topic = **0 msgs**. Not backscatter — washed out.
- My earlier "UDP loss/reorder" hypothesis is **REFUTED**: lines are consistent
  length, so transport is fine.
- Running the driver's OWN `dark_layer` model over the raw GCV-20 pcap reproduces
  the bag exactly (2076 B, mean 211, 97% >127, clearvu dropped). **So the fault
  is the driver decode, not transport/proxy/rqt.**

ROOT CAUSE (definitive): **driver decode `dark_layer()` (`decode.py`/`PingAssembler`).**
The driver ported the colleague's `gcv_decode2.py` v2 model ("high-res echo = bytes
after the LAST sh/shs signature"). On the GCV-20 each eb07 packet has TWO layers:
`fh(da04d804)@28 .. sh(ae02ac02) .. end`. The driver takes the *last* layer (~300 B
/packet → 2076/line) = a low-res/AGC "display" layer → washed out. It also drops
clearvu (no sh sig → empty). **Both reference decoders are wrong**: v2 (=driver) =
washed-out last layer; v1 (`pl[20:]`) = the first layer but with a `50,179`
two-byte interleave ("blocky banding", per v2's own changelog).

DECODE IS UNSOLVED. Explored further: the first layer (FH→SH) is a 2-byte
interleave; its **even** stream `[0::2]` has a real nadir→far DECAY profile
(98→43) + full 0-255 range (promising!), but rendered to PNG it's mostly noise
with per-packet vertical seams — so even-stream-of-layer0 across packet
boundaries is still not the clean echo (per-packet phase drift and/or far-range
noise floor). Needs more reverse-engineering than a triage pass should guess.

Secondary (real, easy): `sample_rate_hz` defaults to 0.0 → rqt `range_max`
(gated on sample_rate>0) never computed → no range axis. And clearvu is silently
dropped by the signature decode.

NEXT STEPS (not a quick patch — this is a decode RE problem):
- Loop in the colleague who captured the GCV-20 / their latest decoder; the
  protocol layer+interleave structure isn't cracked.
- Systematic RE with VISUAL validation (render PNG per hypothesis — offline from
  the pcaps in `~/garmin_sidescan/captures/`) against a known target / the GCV's
  own chartplotter rendering. Resolve: which layer, de-interleave phase per
  packet, what the odd stream is.
- Meanwhile (low-risk, independent of the decode fix): set a real `sample_rate_hz`
  default + restore clearvu.
Did NOT patch the driver: two hypotheses (16-bit; layer0-even) already failed
empirical/visual checks — shipping a guessed decode would just produce a
different wrong image.
