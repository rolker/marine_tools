---
issue: 8
---

# Issue #8 — Field import: marine_tools (2026-05-01)

## External Review
**Status**: complete
**When**: 2026-05-18 13:09
**By**: Claude Code Agent (Claude Opus 4.7 (1M context))

**PR**: #9 — 3 review(s), 0 valid, 0 false positives (18 bot comments, all addressed by later commits or informational)
**CI**: no checks reported on branch (rolker/marine_tools has no GitHub Actions)

### Actions
- [ ] (Optional) Decide whether `reconnect_delay_sec`, `stale_age_warn_sec`, `stale_age_error_sec` should be promoted to launch arguments alongside the safety knobs in `zda_serial.launch.py`. Implementer's commit `b7e09c3` deliberately scoped the change to safety params; the three remaining knobs are operational tuning with safe defaults. Defer or extend — user's call.
- [ ] (Optional) Re-request Copilot review now that the branch is at `fad57807` — every prior finding is resolved, so a clean pass would close the loop on the PR.

## External Review
**Status**: complete
**When**: 2026-05-18 13:30
**By**: Claude Code Agent (Claude Opus 4.7 (1M context))

**PR**: #9 — 1 new review against HEAD `fad5780`, 3 valid + 1 optional + 0 false positives (5 bot comments)
**CI**: copilot-pull-request-reviewer = success (no other checks configured)

### Actions
- [ ] **Fix:** Add parameter validation after `__init__` line 115 — raise `ValueError` if `_stale_warn > _stale_error` or any of `_stale_warn`/`_stale_error`/`_startup_grace`/`_reconnect_delay` is negative. Add a `test_node.py` unit test.
- [ ] **Fix:** Lift blocking serial I/O out from under `self._lock`. In `_open_serial`, construct `serial.Serial(...)` outside the lock and atomically swap `self._serial` under it. In `_on_utc_time` write path, snapshot the serial reference under the lock, drop the lock for `write()`, then re-acquire briefly to update counters / `_last_emit_ns`. Move the `_last_msg_ns` / `_gate_state` / `_suppressed_count` writes at node.py:192–212 inside the lock (resolves the snapshot-comment honesty issue at lines 250–256 in the same change).
- [ ] (Optional) Add a one-liner "Tests added: `test_node.py`, `test_zda.py`" section to the PR body so the bot's stale "Test plan" comparison stops re-firing.
- [ ] After fixes pushed, re-request Copilot review.
