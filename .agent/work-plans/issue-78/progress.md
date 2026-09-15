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

## Plan Review
**Status**: complete
**When**: 2026-09-15 10:13 -04:00
**By**: Claude Code Agent (Claude Opus)

**Plan**: `.agent/work-plans/issue-78/plan.md` at `11242d6`
**PR**: PR-less (`--issue` mode, dispatched sub-agent — independent of the plan author)
**Verdict**: changes-requested

Evaluation: scope Good (single PR, two source + two test files); issue alignment
Good (all four review-issue actions are answered); file targeting Needs work (test
file split, see F7); consequences Needs work (fragment semantics and the #89
conflict surface, F1/F8); documentation impact Good; principle alignment Needs work
("Test what breaks" — the drop-oldest test as written asserts the wrong thing, F4);
ADR compliance Good (ADR-0008 param pattern, ADR-0013 vocabulary); ROS conventions
Good (static param, declare + construct-time `ValueError`, additive `KeyValue`, no
new topic — matches the operator's no-added-bag-volume preference).

### Findings
- [ ] (must-fix) Drop-oldest leaves a mid-sentence fragment at the buffer front, and the next terminator frames it as a whole sentence — for `RegexParser` a fragment can `search`-match and publish a plausible-but-wrong sound speed on the RELIABLE `sound_speed` topic. The cap must mark the residue suspect and discard through the next terminator; plan has no such rule — `plan.md` step 1 (drop-oldest bullet)
- [ ] (must-fix) Trim-before-frame drops complete framable sentences: `_append_and_trim` trims on append, before `feed()`'s framing loop runs, so any `ser.read(256)` chunk (`node.py:178`) larger than the cap loses whole terminated sentences silently (+1 on a trim counter). Safe at the 4096 default, silent data loss at any configured cap < 256. Trim the *residue at the end of `feed()`* instead (cap then means "max unframed residue", independent of chunk size), or enforce a floor >= the serial read size — `plan.md` step 1
- [ ] (must-fix) The stated field mechanism is contradicted by the code it cites: `AMLParser._TERMINATOR` is a bare `\r` (`parsers.py:75,89`), so LF->NUL alone never stalls the AML buffer — only corruption of the `\r` itself does. Name which parser the field unit ran and restate the mechanism; the 4 KiB sizing rationale rests on it — `plan.md` Context
- [ ] (must-fix) Related, and not fixed by this issue: under LF->NUL on the `aml` parser the NUL survives framing (`lstrip(b'\n')` at `parsers.py:88` does not strip NUL), so every subsequent sentence parses NaN (verified: `Decimal('\x001500.0')` raises `InvalidOperation`). #78's cap does nothing for that failure. File a follow-up so #78 is not recorded as the field fix — `plan.md` Context
- [ ] (must-fix) The plan's own drop-oldest test asserts the wrong outcome: feeding unframed garbage past the cap then "a well-formed sentence" frames `garbage+sentence` as one line and yields NaN, not a correct reading. The test must flush the fragment with a terminator first, then assert the *following* sentence frames — `plan.md` step 8
- [ ] (suggestion) "never truncating a real sentence" is not true of the case the cap exists for: during a stall the framed unit is the glued multi-sentence line (field data shows sentences joined by `\r\x00`), and 4 KiB truncates it by ~3 orders of magnitude. The 4096 default is otherwise well argued and accepted (~5 s at 800 B/s, 16x the 256 B read chunk, 16x the longest configured regex line) — state the loss honestly, and that #77's `serial_tap` is the recovery path for the dropped bytes — `plan.md` step 2
- [ ] (suggestion) Count bytes dropped, not trim events: at 25 Hz a stall produces one trim per chunk, so `buffer_trim_count` is a proxy for elapsed time; bytes-dropped is the actionable number for both the `KeyValue` and the WARN text — `plan.md` steps 1, 4, 6
- [ ] (suggestion) 1 Hz WARN over a 1-5 h field stall is ~18k log lines, and `_publish_diagnostics` already reports ERROR (stale reading) throughout that window, so the WARN adds little after the first. Back off after the first trim (e.g. <=1/60 s) — `plan.md` step 5
- [ ] (suggestion) `RegexParser` tests live in `test/test_regex_parser.py`; `test/test_parsers.py` is AML-only. The plan puts both parsers' new tests in `test_parsers.py` — `plan.md` step 8, Files to Change
- [ ] (suggestion) The #89 conflict surface is two files, not one: PR #89 also edits `test_node.py` and node docstrings. State that whoever merges second rebases, and that #89 (4 review rounds, field check still open) re-runs its 62-test suite — `plan.md` Branch sequencing
- [ ] (suggestion) Add a boundary test for a terminator straddling the trim (a kept `\r` with `\n` next, and a trim cutting inside `\r\n`). Verified benign today — `RegexParser`'s `line.strip()` and `AMLParser`'s `lstrip(b'\n')` absorb the orphan — but nothing pins it — `plan.md` step 8
- [ ] (suggestion) Record that the cap also bounds the pre-existing per-chunk O(n) cost (`lstrip` + slice rebuild the whole buffer each framing iteration); trimming itself is O(cap) = 4 KiB per chunk at 25 Hz, negligible. Also move `self._buffer` initialization into the ABC alongside the helper that mutates it, and add the new parameter to the package README when #88 lands — `plan.md` steps 1, 7

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 10:35 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: changes-requested

**Branch**: feature/issue-78 at `a6d3803`
**Mode**: pre-push
**Depth**: Deep (reason: 1161 changed lines, 200+ threshold; concurrency + RELIABLE-topic data integrity)
**Must-fix**: 2 | **Suggestions**: 7
**Round**: 1 | **Ship**: continue — two mechanical must-fixes (an operator-facing counter that undercounts, and a deferral target that does not carry the deferred items); address then re-review.

Specialists: Static Analysis (ament flake8 + pep257, clean), Governance, Plan Drift,
Claude Adversarial Lens A + Lens B. Copilot and local-model passes off (not opted in).
Tests: `76 tests, 0 errors, 0 failures, 0 skipped` (49 on `jazzy`). Mutation check:
12 targeted mutations (cap removed, discard-through-terminator removed, trim moved
from residue to append, drop-newest, off-by-one at the cap, both validations removed,
param not plumbed, back-off removed, reset rule removed, KeyValues removed) — all 12
killed by the new tests. Plan drift: none; commit `a6d3803`'s plan sync is honest.

### Findings
- [x] (must-fix) `buffer_dropped_bytes` undercounts real stream loss: `_resync` discards the head fragment through the terminator without adding it to the counter, contradicting the ABC docstring's "how much of the stream was lost" contract and the node's WARN text — `sound_speed_bridge/sound_speed_bridge/parsers.py:162`
- [x] (must-fix) The README consequence is deferred to rolker/marine_tools#88, but #88's filed ask list does not mention `parser_max_buffer_bytes`, `buffer_dropped_bytes` or `buffer_trim_count` — the deferral points at a list that will silently omit them — `.agent/work-plans/issue-78/plan.md:228`
- [ ] (suggestion) `parser_max_buffer_bytes` is declared without `read_only=True` while the code comment calls it static, so a field `ros2 param set` reports success and silently does nothing — `sound_speed_bridge/sound_speed_bridge/node.py:78`
- [ ] (suggestion) Back-off quiet-period reset is anchored to the last WARN, not the last trim, so the docstring's "a full ceiling passes with no further trims" overstates it; impact is bounded by the 300 s ceiling — `sound_speed_bridge/sound_speed_bridge/node.py:188`
- [ ] (suggestion) The two counters are a correlated pair read non-atomically across threads, and `_publish_diagnostics` re-reads them after `_warn_on_buffer_trim`, so the WARN text and the published KeyValues can disagree by one chunk; the "same GIL-atomic pattern" comment overstates the guarantee for a pair — `sound_speed_bridge/sound_speed_bridge/node.py:128`
- [ ] (suggestion) Reachable serial-thread death (pre-existing, untouched): `int(decimal_value * 1000)` sits outside the try, so a sentence of `nan`/`inf`/`snan` raises `ValueError`/`OverflowError` past `_serial_loop`'s `(SerialException, OSError)` catch, killing the daemon thread with `_serial_connected` left True; eager `feed()` widens the loss to the whole chunk. Verified by execution — file a follow-up beside #90 — `sound_speed_bridge/sound_speed_bridge/parsers.py:253`
- [ ] (suggestion) `main()` constructs the node outside its `try/finally`, so the new parameter `ValueError` exits with a raw traceback and no `rclpy.shutdown()`; the fail-loud intent is right but is documented nowhere an operator sees — `sound_speed_bridge/sound_speed_bridge/node.py:415`
- [ ] (suggestion) The package's only example launch file does not surface the new operator-tunable parameter, unlike `device`/`baud`/`frame_id` — `sound_speed_bridge/launch/aml_svs.launch.py:15`
- [x] (suggestion) Plan's Files-to-Change names the node attribute `_last_buffer_dropped_bytes`; the code calls it `_last_warned_dropped_bytes` — cosmetic — `sound_speed_bridge/sound_speed_bridge/node.py:133`
