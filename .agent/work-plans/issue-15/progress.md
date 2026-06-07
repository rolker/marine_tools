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

Secondary (real, easy): `sample_rate_hz` defaults to 0.0 → rqt `range_max`
(gated on sample_rate>0) never computed → no range axis. And clearvu is silently
dropped by the signature decode.

> **SUPERSEDED 2026-06-07** — an interim note here called the layer0 encoding
> "unsolved" after the 16-bit / even-interleave / RLE guesses failed. That was
> wrong: the failures were method bugs (notably de-interleaving the *concatenated*
> line instead of *per packet*). The decode was subsequently CRACKED — see
> "★★ DECODE CRACKED" at the end of this entry.

### C1 RE session (2026-06-07) — decode reverse-engineering, partial
Assets confirmed available for offline RE (no boat needed):
- `~/garmin_sidescan/captures/` 8 pcapng from 2026-06-05: gcv20_{passive,rangesweep,
  settings,nogpsmap,commands,toggles,bringup}, gcv10. rangesweep/settings are the
  controlled-variable captures. Bags `~/data/logs/bizzy_sidescan/*` are decoded
  (broken) output, less useful than the pcaps.
- Colleague tooling: `gcv_decode.py` (v1), `gcv_decode2.py` (v2), `gcv_ros_node.py`,
  `gcv20_{multi,state}_diff.py`.

Findings:
- `gcv_ros_node.py` imports v2 `dark_layer` — the colleague's ROS node uses the SAME
  washed-out decode. No better reference exists; the marine_tools driver is a faithful
  port of a decode that was never actually correct.
- GCV-20 eb07 packet = 20B header, then FH(`da04d804`)@~27, then layer0 (~613B,
  variable), then SH(`ae02ac02`), then layer1 (~304B, fixed). 6 full + 1 short per
  channel run.
  - layer1 (what the driver/v2 take) = washed-out (mean ~211, 97% >127, flat) — a
    low-res/AGC DISPLAY layer, NOT the echo.
  - layer0 = larger, has full 0-255 dynamic range, byte-paired structure
    (even-idx values 0-255, odd-idx bounded <=179), variable per-packet length.
- Interim encoding guesses (16-bit LE; (value,count) RLE) were wrong; the even/odd
  interleave guess was right in spirit but failed because it was applied to the
  concatenated line, not per packet.
- rangesweep: full-run layer sizes are CONSTANT across range (layer0=3683, layer1=1824,
  6 pkts) — range is metadata, not bin count (range = ping timing, set via command).

## ★★ DECODE CRACKED (2026-06-07) — GCV-20 echo = layer0 odd bytes, per-packet

Method: decoded the GCV-10 BUCKET capture (`captures/gcv10_*.pcap`) with the known-good
`gcv_decode2.py` dark-layer path → reference signature = clean near→far DECAY
(ch0 profile 212→0; bright nadir, nothing past the bucket). Then searched GCV-20 bench
extractions for one that reproduces that decay. (Working-image reference for "what real
looks like": `gcv_decode2.py` on Dan's GCV-10 survey `extracted/.../dumpcap_file.pcap`
→ `decoded2_sidescan.png`.)

Result — for the GCV-20 (`gcv20_passive`/`gcv20_settings`, ch0):
| extraction | profile near→far | verdict |
|---|---|---|
| last/dark layer (driver) | flat ~211 | washed-out display layer — WRONG |
| layer0 even bytes | flat ~125 | flat companion stream (purpose TBD) |
| **layer0 odd bytes, per-packet** | **decays (119→41)** | matches GCV-10 ref ✓ |

So **GCV-20 sidescan echo = the ODD-indexed bytes of layer0 (FH→SH), de-interleaved
WITHIN each packet, then concatenated**. The earlier "noise" was from de-interleaving
the concatenated line (layer0 per-packet length flips 613/614 → phase scramble);
per-packet de-interleave fixes it. Rendered PNG shows the reference's near→far decay
(`.agent/scratchpad/gcv20_passive_ch0_layer0odd.png`).

GCV-20 ping grammar (from `gcv20_passive`): `ch2(ClearVu,7pkts) M ch1(stbd,7) M
ch0(port,7) M M` per ping; sidescan pkts 6×953+1×797, clearvu 6×648+1×544. Port/stbd
are SYMMETRIC (both 7 pkts/run, same sizes) → ~2089 echo bins/ping each (the earlier
"ch0 1841 vs ch1 920" was a render min-width-truncation artifact, NOT real).

Generation difference: GCV-20 packets carry GCV-10's first two layers but NOT GCV-10's
third "dark" layer — so on GCV-20 the echo is interleaved in layer0, while the driver
(ported from the GCV-10 path) wrongly takes the last layer.

Boat install check (from the washed-layer bag, render `.agent/scratchpad/boat_sidescan_v2.png`):
gross geometry looks CORRECT — straight centered nadir, port/stbd symmetric, stable over
~1770 pings. So the on-boat "looked wrong" was the DECODE (wrong layer), not the mount.

Boat data is UNRECOVERABLE for re-decode: `dark_layer` discarded layer0 before writing
the bag; both bags hold only the 2076-B last layer (lossy). Final SEAFLOOR validation of
the layer0-odd decode needs a fresh wet capture: `tcpdump -i <marine-net-iface> -w
gcv20_water.pcap 'udp port 50220'` (GPSMAP master present, over real bottom).

`sample_rate` fix (decode-independent): derive from the COMMANDED range, not a constant —
`sample_rate = sound_speed * samples_per_beam / (2 * range_m)` (driver owns range_m,
sound_speed from SV topic, bins from len(samples)). Also restore the dropped clearvu channel.

Status: GCV-20 decode = bench-validated (decay signature vs GCV-10 ref); pending seafloor
confirmation on a wet capture. Driver patch deferred to a discuss-then-implement step
(output/params review with Roland first).

### C1 driver implementation (2026-06-07) — landed on PR #17
Implemented after the output/params discussion with Roland:
- `decode.py`: `echo_layer()` (GCV-20: per-packet FH-layer odd bytes) added; `dark_layer()` kept (GCV-10); `PingAssembler(extractor)` now pluggable. (`f81331b`)
- `node.py`: `device` auto-detect (packet geometry, GCV-10 >1000B vs GCV-20 ≤953) selects the extractor, `gcv20`/`gcv10` force it, mismatch warned (wrong = silent gibberish, no crash); **self-rendered `~/waterfall_*` removed** (Roland: doesn't belong — rendering is rqt_sonar_waterfall's job; dropped waterfall_height/_rate_hz/publish_waterfall/range_bins params); **`~/debug/raw`** (UInt8MultiArray) gated by runtime-settable **`debug_raw`** param publishes every raw UDP payload so a bag is fully re-decodable offline (kills the lossy-bag problem for the next wet run); `sample_rate` derived from commanded range. README updated. (`1ad6175`)
- Tests: 33 pass, lint clean. Integration-checked: committed `echo_layer`+`PingAssembler` decode the GCV-20 bench pcap to decaying port/stbd (~2096 bins, symmetric) + clearvu (~2104).
- STILL bench-validated only — the wet-capture seafloor confirmation is now trivial to obtain: set `debug_raw:=true`, `ros2 bag record …/debug/raw` on the next wet GCV-20 run, then re-decode offline. No tcpdump needed.

### C1 follow-up: even stream identified → GCV-20 echo is 16-bit (2026-06-07)
The "even companion stream" is NOT a separate product — it's the **low byte (LSB)
of a 16-bit little-endian sample**. Raw bytes (not averages) show the odd stream
is smooth (jaggedness ~6) = the echo MSB, the even stream is full-range uniform
(jaggedness ~88) = the LSB; reconstructing layer0 as uint16-LE gives a smooth,
coherent high-dynamic-range signal (e.g. `[17399,17929,18410,...]`, max ~62000,
with real features). (Earlier "even ≈ flat 127" was an averaging artifact.)

So `echo_layer` now returns the first layer as raw uint16-LE bytes (even-trimmed)
and the node publishes `DTYPE_UINT16` for GCV-20 (UINT8 for GCV-10) — dropping the
de-interleave and keeping full dynamic range (the prior odd-only decode was a
correct but 8-bit-truncated MSB view). Bench-verified on the GCV-20 bucket pcap:
port/stbd 2096 bins, clearvu 2104, all decay near→far, ~16-bit range. 34 tests
pass. (`829524c`) Open: whether the LSB is true precision vs dither — a wet-scene
capture settles it; uint16 captures it faithfully either way.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-07 14:31 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: changes-requested

**Branch**: feature/issue-15 at `10786e4`
**Mode**: pre-push
**Depth**: Deep (new safety-critical sonar driver); reviewed delta = decode/node/tests since R5 (`bd6e5cb..HEAD`)
**Must-fix**: 1 | **Suggestions**: 3
**Specialists**: Static (clean) + Claude Adversarial + Copilot Adversarial; no plan (plan-drift skipped)

### Findings
- [ ] (must-fix, cross-confirmed: Claude Adv + Copilot Adv) device auto-detect decides `gcv20` from *absence* of a >1000B packet (40-count fallback) — a GCV-10 starting with only small packets (ClearVu-only / partial start) silently latches gcv20 -> wrong extractor+dtype, never re-evaluates. Fix: positive signal (>1000->gcv10, >900->gcv20, else wait) — `node.py:_observe_geometry`
- [ ] (suggestion) `echo_layer` `find(FH)` scans from offset 0; a coincidental FH pattern in the header shifts extraction — start at CHANNEL_OFFSET — `decode.py:echo_layer`
- [ ] (suggestion) `echo_layer` couples FH->SH / FHS->SHS; accept either SH/SHS terminator as insurance — `decode.py:echo_layer`
- [ ] (suggestion) `device` param unvalidated; a typo silently falls to auto — validate + warn at startup — `node.py:__init__`

### Verified clean (both adversarial passes)
- cross-thread state (_assembler/_detected_gen/_sonar_dtype/_bytes_per_sample all rx-thread-only; _debug_raw/_controls GIL-atomic), uint16 alignment + is_bigendian, sample_rate divide-by-zero/NaN guards, _detect_sizes bounded, empty/short-payload handling.

### Pre-push review findings resolved (2026-06-07) — `069ec71`
- [x] (must-fix, cross-confirmed) device auto-detect from packet-size absence → replaced with positive sub-header **tag-byte** signal (offset 13: 0x11=GCV-10, 0x12=GCV-20; verified 100% across both captures, every channel). Decides on the first eb07 packet; ClearVu-only stream now identifies correctly (644/647B → right gen). Dropped the size heuristic + `_detect_sizes`. `node.py:_detect_generation`, `decode.py:GEN_BY_TAG`
- [x] (suggestion) `echo_layer` `find(FH)` now starts at CHANNEL_OFFSET (coincidental header pattern can't shift the start)
- [x] (suggestion) `echo_layer` accepts either SH/SHS as first-layer terminator (not coupled to opener)
- [x] (suggestion) `device` param validated at startup (warn + fall back to auto on unknown value)
- Tests: 37 pass (3 new tag-detection tests, decode + node-method); lint clean. Integration-verified on both pcaps incl. ClearVu-only packets.

### Stream-type indicators in the message + ClearVu auto-classify (2026-06-07) — `7f460b3`
Two related improvements (from Roland's stream-type question):
- **Message-level geometry**: `rx_angles`/`tx_angles` were all `[0.0]` — streams were distinguishable only by topic name. Now set per side: **+`beam_angle_deg` (port), −(starboard), 0 (ClearVu down-look / water column)**; default 90°, param `beam_angle_deg`. A consumer can now read stream geometry from the message (the ±90° sidescan convention Roland recalled).
- **Intrinsic ClearVu auto-classify**: render-layer byte at offset 8 = `0x0d` for the ClearVu down-look beam on BOTH generations (SideVu = 0x0e/0x0f) — `decode.is_water_column()`. `node._classify_beam()` records each channel's beam type and warns once on a channel-map mismatch (a channel routed to a SideVu topic but carrying the ClearVu beam), independent of the unit-specific channel numbers. Note: distinguishes beam *type* (sidescan vs water-column), NOT port vs stbd — both SideVu sides share the layer byte, so port/stbd still comes from the channel map.
- Verified on both captures (GCV-10 ch5 / GCV-20 ch2 = water column). 41 tests pass, lint clean.

### Stream geometry reworked to spec + TF; de-trademarked naming (2026-06-07) — `1b16a70`
Corrects the earlier (wrong) rx_angle approach after reading the message spec + driver precedents (Roland's steer):
- `rx_angles`/`tx_angles` per the RawSonarImage spec are the *steering* angle (a multibeam concept). A fixed single-beam sidescan applies none → **[0.0]**. Dropped the invented ±90° look-direction and the `beam_angle_deg` param. Confirmed against the **EdgeTech** driver (0.0 + topic) and **r2sonic** (single-beam fallback 0.0); rejected baking mounting into the message.
- **Orientation via per-channel `frame_id` + TF**: each channel publishes `<frame_id>_port`/`_starboard`/`_down`; the URDF/TF tree orients each transducer (side + tilt). Non-traditional/backwards mounting = pure TF edit, never code/param. Likely what the EdgeTech field fix (never committed) added.
- **De-trademarked**: down-look topic/param/side `clearvu` → generic `down` (`sonar_image_down`, `down_channels`, `freq_down_hz`); prose ClearVü→down-look, SideVü→side-scan. Kept the `pl[8]` down-look auto-classify (`is_water_column`, `_classify_beam`).
- Proper sidescan rviz (slant-range correction, water-column skip, terrain/nadir-plane draping; down-look + GCV depth report as the altitude feed) = separate effort/issue. 40 tests pass, lint clean.
