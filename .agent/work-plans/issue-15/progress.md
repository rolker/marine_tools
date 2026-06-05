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
