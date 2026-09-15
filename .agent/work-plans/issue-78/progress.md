---
issue: 78
---

# Issue #78 — parsers: unbounded accumulation buffer — cap + trim WARN (misconfigured line terminator can emit one multi-MB sentence)

## Issue Review
**Status**: complete
**When**: 2026-09-15 10:05 -04:00
**By**: Claude Code Agent (Claude Sonnet)

**Issue**: rolker/marine_tools#78
**Comment**: (best-effort post follows this entry; not recorded inline)
**Scope verdict**: well-scoped

### Findings

Verified against `sound_speed_bridge/sound_speed_bridge/parsers.py` (current
`feature/issue-78` branch, based on `jazzy`): both `AMLParser.feed()` (buffer
at `parsers.py:78,82,88,94`) and `RegexParser.feed()` (buffer at
`parsers.py:158,162,171`) accumulate into an unbounded `self._buffer`. The
issue's description is accurate — no `maxlen`/truncation anywhere in either
class, and a terminator that never matches (misconfigured
`regex_line_terminator`, or the field-observed AML LF->NUL UART collapse,
rolker/unh_echoboats_project11#163) grows the buffer for as long as bytes
keep arriving.

`sound_speed_bridge/sound_speed_bridge/node.py:222` confirms the consequence
is live: `raw_bytes` (from `SoundSpeedReading`) is published unconditionally
on the RELIABLE `raw` topic on every parsed sentence, including one produced
from a multi-MB accumulated buffer. PR #76 (merged, `raw` topic) and the
`sound_speed` deployment-bag routing this issue cites are both present on
this branch's ancestry.

### Scope Assessment

**Well-scoped?** Yes. The ask (cap the buffer, drop-oldest beyond a
configurable max, throttled WARN + diagnostics counter) is a single,
self-contained change to `AMLParser`/`RegexParser` plus one new
`/diagnostics` `KeyValue` in `node.py`. Fits one PR.

**Right repo?** Yes — `marine_tools` owns `sound_speed_bridge`, the package
with the defect.

**Dependencies**:
- PR #89 (issue #77, `serial_tap`) is **currently open**, based on
  `feature/issue-77`, and also touches `_publish_diagnostics`'s `KeyValue`
  list in `node.py` (adds `tap_byte_count`, `tap_error_count`,
  `serial_tap_enabled`). PR #89's own body states the *parsing* concern is
  independent ("the tap point is upstream of the parser's accumulation
  buffer; no code coordination needed") — verified true: the tap sits in
  `_serial_loop` before `self._parser.feed()` is called, so #78's buffer cap
  never touches tap code. The **only** overlap is textual: both issues add
  entries to the same `KeyValue` list literal in `_publish_diagnostics`
  (`node.py:299-312`), which is a merge-conflict/ordering question, not a
  design coordination. Recommend plan-task decide only whether #78's branch
  rebases onto #77 post-merge or lands independently and picks up a trivial
  rebase conflict later — not a scope blocker either way.
- PR #76 (merged) is the routing that made this issue's consequence
  reachable; already landed, no action needed.
- rolker/unh_echoboats_project11#163 and #396 are cited as the field
  motivation; no code dependency on either (that repo is not touched by this
  issue).

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Test what breaks | Action needed | The ask explicitly wants a cap + drop-oldest behavior and a throttled WARN — this needs a dedicated regression test that feeds > max_bytes with no terminator and asserts (a) the buffer is bounded, (b) old bytes are dropped not new ones (so a terminator arriving later still frames the newest data), (c) the WARN is throttled not per-chunk, (d) a new diagnostics counter increments. `test_parsers.py` exists (`sound_speed_bridge/test/test_parsers.py`) and is the natural home. |
| A change includes its consequences | Action needed | `node.py:299-312`'s `KeyValue` list is the `/diagnostics` contract; the ask's "diagnostics counter" must land there (same shape as `parse_error_count`, `udp_send_error_count`). No package README exists for `sound_speed_bridge` today (only `parsers.py`'s module docstring documents buffer/framing behavior) — the docstring at `parsers.py:1-10` should note the new cap since it already documents framing quirks. |
| Human control and transparency | Watch | The ask says "configurable max" — needs a new `declare_parameter` (e.g. `parser_buffer_max_bytes` or similar) in `node.py`'s constructor alongside the existing `regex_*` parameters, with a sane default (issue notes ~1 KB/s so a default in the hundreds-of-KB to low-MB range comfortably covers transient stalls without defeating the cap's purpose — plan-task should pick and justify a concrete number). |
| Only what's needed | OK | Drop-oldest with a max byte count is the minimal fix matching the ask; no speculative generalization needed (e.g. no need to make this pluggable across parser types beyond the shared base class). |
| Improve incrementally | OK | Small, isolated change to two parser classes + one diagnostics field. |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| 0008 — ROS 2 conventions | Yes (lightly) | New parameter must follow existing `declare_parameter` + validation patterns already used in `node.py` (e.g. the `regex_line_terminator` validation raising `ValueError` at construction). |
| 0013 — progress.md vocabulary | Yes | This entry follows the vocabulary; downstream phases (plan-task etc.) must continue to do so. |
| Others (0001-0007, 0009-0010) | No | Not triggered — no new tooling, packaging, or task-runner changes. |

### Consequences

- `/diagnostics` `KeyValue` list in `node.py` gains a new counter (coordinate
  ordering/rebase with PR #89 if both are in flight, per Dependencies above).
- `parsers.py`'s module/class docstrings should describe the new cap
  behavior since they already document framing quirks in prose.
- No `sound_speed_bridge` package README exists to update (verified: none
  found under `sound_speed_bridge/`) — not a gap introduced by this issue,
  pre-existing state.
- Host-injected context flagged a design point for plan-task to record (not
  decide here, per host instructions): whether to stack #78 on
  `feature/issue-77` or keep `node.py` edits minimal and rebase after PR #89
  merges. Recorded above under Dependencies/Principle Alignment; left as an
  open plan-task decision, not resolved by this review.

### Recommendations

- Plan-task should pick a concrete default for the new buffer-cap parameter
  and state the rationale (issue gives a rate estimate — ~1 KB/s at 9600
  baud — as a sizing anchor).
- Plan-task should decide the #77/#78 sequencing question (rebase order)
  called out above; it is a merge-mechanics choice, not a design coupling,
  so either order is acceptable as long as it's stated.
- When the WARN throttle is implemented, follow the pattern (if any) already
  used elsewhere in this repo for throttled logging (checked: no existing
  throttled-WARN precedent in `sound_speed_bridge`; a plain
  time-since-last-warn gate is fine given the audience is a single log
  stream).

### Actions
- [ ] Add a dedicated regression test in `sound_speed_bridge/test/test_parsers.py` covering: buffer cap enforced, drop-oldest semantics, throttled WARN, new diagnostics counter increments.
- [ ] Add the new diagnostics counter to `node.py`'s `_publish_diagnostics` `KeyValue` list; coordinate ordering with PR #89 (open, touches the same list) — decide stacking vs. independent-then-rebase.
- [ ] Update `parsers.py` docstrings (module + affected classes) to describe the new cap/drop-oldest behavior alongside the existing framing-quirk documentation.
- [ ] Pick and justify a concrete default for the new configurable max-buffer-size parameter.

## Plan Authored
**Status**: complete
**When**: 2026-09-15 15:20 -04:00
**By**: Claude Code Agent (Claude Sonnet)

**Plan**: `.agent/work-plans/issue-78/plan.md` at `11242d6`
**Branch**: feature/issue-78 at `11242d6`
**Phases**: single

### Open questions
- [ ] No open questions — plan is review-plan-ready.
