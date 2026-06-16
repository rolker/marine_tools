---
issue: 46
---

# Issue #46 — kongsberg_em_bridge: option to save received datagrams to a .all file

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-15 23:14 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: approved

**Branch**: feature/issue-46 at `2c66628`
**Mode**: pre-push
**Depth**: Standard (reason: ~130-line feature touching driver recv/lifecycle)
**Must-fix**: 1 (fixed before push) | **Suggestions**: 1 (declined, rationale below)

### Findings
- [x] (must-fix) Shutdown race: recv thread `_record` write vs `destroy_node` close of `self._save_file`; write-to-closed-file raises ValueError not caught by OSError-only handler — both adversarial lenses converged. Fixed with a `threading.Lock` guarding the None-check/write/close and by catching `(OSError, ValueError)` — `kongsberg_em_bridge/node.py`
- [ ] (suggestion) Declined: flush-per-write under high datagram rate. `flush()` pushes to the OS page cache (not `fsync`), real M3 rates are a few hundred datagrams/s, and per-write flush preserves crash-safety of the recording — `kongsberg_em_bridge/node.py`

### Notes
- Static analysis: ament flake8 + pep257 clean via `colcon test` (13 tests, 0 failures).
- Functional: node + `replay --all-types` produced a valid little-endian `.all` (0 non-STX records; A/G/N/X/C datagram types present; N/78 re-parses) and clean shutdown with no traceback.
