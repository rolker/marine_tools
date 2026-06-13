---
issue: 37
---

# Issue #37 — garmin_sidescan: ping assembler splits pings on the d807 telemetry sub-form (020c = water temperature)

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-13 09:30 -04:00
**By**: Claude Code Agent (Claude Fable 5)
**Verdict**: approved (with hardening applied)

**Branch**: feature/issue-37 at `04fd9dd`
**Mode**: pre-push
**Depth**: Standard (reason: driver decode/assembly change + new publisher, ~193 lines)
**Static analysis**: run via colcon (flake8/pep257/pytest) — clean
**Copilot Adversarial**: skipped (monthly quota exceeded)
**Must-fix**: 1 (addressed) | **Suggestions**: 2 (addressed)

Independent fresh-context Claude adversarial pass. The split-on-020c fix and the
temperature decode/publish are sound; the reviewer confirmed the tag discriminator
is unambiguous (a delimiter's field2 tag 0x10|L can never read as 0x0c), the
Time-math units are correct, and the regression test genuinely distinguishes
old (2 fragments) from new (1 whole ping). End-to-end re-decode of
bag_2026-06-12T16.06.52 drops short pings from 140/82/122 to 2/0/0 per channel
(residual = genuine UDP loss); 7,692 temperatures decode at 25.2-29.0 degC.

### Findings
- [x] (must-fix) corrupt telemetry → NaN/inf published, and NaN defeats dedup (`nan != nan`) — `decode.py marker_temperature_c` now drops non-finite; `node.py _emit_temperature` gates an implausible window
- [x] (suggestion) truncated `02 0c` frame neither flushed nor decoded — split `_is_telemetry_marker` (structural) from validity so a short frame flushes (`decode.py is_run_delimiter`)
- [x] (suggestion) "next channel backstops a dropped delimiter" stated as a guarantee — softened to the observed channel-cycling behaviour (`decode.py feed` comment)

### Static analysis / tests
flake8 + ament_pep257 clean; pytest 73 tests, 0 failures (new: real-capture-frame
decode, non-finite rejection, truncated-flush, temperature_plausible,
temperature_publish_due, telemetry-mid-run assembles whole).
