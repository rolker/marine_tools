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
- [x] **Fix:** Add parameter validation after `__init__` line 115 (commit `7c45153`).
- [x] **Fix:** Lift blocking serial I/O out from under `self._lock` (commit `c7ef31b`).
- [x] (Optional) Add a "Tests added" section to PR body (via `gh api PATCH` — `gh pr edit --body-file` was the silent classic-Projects no-op).
- [x] After fixes pushed, re-request Copilot review (user clicked Reviewers → Copilot in the web UI; the API path silently no-ops — see `reference_copilot_review_no_api_trigger.md`).

## External Review
**Status**: complete
**When**: 2026-05-18 17:00
**By**: Claude Code Agent (Claude Opus 4.7 (1M context))

**PR**: #9 — Copilot re-review against HEAD `c7ef31b`, 4 valid + 0 false positives.
**CI**: copilot-pull-request-reviewer = success (run 26047546692).

### Actions
- [x] **Fix:** `destroy_node()` annotated `-> bool` but rclpy returns `None` (commit `6d085df`).
- [x] **Refactor:** Drop bare `self._serial is None` pre-check at the top of `_publish_diagnostics` — kept every read of `_serial` under the lock as the file documents (commit `ae9c65b`).
- [x] **Fix:** Re-gating after successful emit was reported as `WARN: ZDA stale` (misleading — bridge isn't broken, gate is functioning). New dedicated branch reports `re-gated: ...` as WARN, escalating to ERROR past `stale_age_error_sec`. UX choice ratified by user (commit `fc1c435`). +2 tests.
- [x] **Fix:** Wrap typed `LaunchConfiguration` substitutions (`baud`, `min_utc_status`, `require_utc_sync`, `startup_grace_sec`) with `ParameterValue(..., value_type=...)`. Works today via YAML coercion on Jazzy but the type-strict wrapper is the documented best practice (commit `bf99fae`).
- [ ] After push, optionally re-request Copilot review (manual click; see memory note).
