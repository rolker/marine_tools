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
- [x] (suggestion) `parser_max_buffer_bytes` is declared without `read_only=True` while the code comment calls it static, so a field `ros2 param set` reports success and silently does nothing — `sound_speed_bridge/sound_speed_bridge/node.py:78`
- [x] (suggestion) Back-off quiet-period reset is anchored to the last WARN, not the last trim, so the docstring's "a full ceiling passes with no further trims" overstates it; impact is bounded by the 300 s ceiling — `sound_speed_bridge/sound_speed_bridge/node.py:188`
- [x] (suggestion) The two counters are a correlated pair read non-atomically across threads, and `_publish_diagnostics` re-reads them after `_warn_on_buffer_trim`, so the WARN text and the published KeyValues can disagree by one chunk; the "same GIL-atomic pattern" comment overstates the guarantee for a pair — `sound_speed_bridge/sound_speed_bridge/node.py:128`
- [x] (suggestion) (deferred: pre-existing, out of #78's scope; host is filing a follow-up beside #90) Reachable serial-thread death (pre-existing, untouched): `int(decimal_value * 1000)` sits outside the try, so a sentence of `nan`/`inf`/`snan` raises `ValueError`/`OverflowError` past `_serial_loop`'s `(SerialException, OSError)` catch, killing the daemon thread with `_serial_connected` left True; eager `feed()` widens the loss to the whole chunk. Verified by execution — file a follow-up beside #90 — `sound_speed_bridge/sound_speed_bridge/parsers.py:253`
- [x] (suggestion) `main()` constructs the node outside its `try/finally`, so the new parameter `ValueError` exits with a raw traceback and no `rclpy.shutdown()`; the fail-loud intent is right but is documented nowhere an operator sees — `sound_speed_bridge/sound_speed_bridge/node.py:415`
- [x] (suggestion) The package's only example launch file does not surface the new operator-tunable parameter, unlike `device`/`baud`/`frame_id` — `sound_speed_bridge/launch/aml_svs.launch.py:15`
- [x] (suggestion) Plan's Files-to-Change names the node attribute `_last_buffer_dropped_bytes`; the code calls it `_last_warned_dropped_bytes` — cosmetic — `sound_speed_bridge/sound_speed_bridge/node.py:133`

## Implementation
**Status**: complete
**When**: 2026-09-15 10:44 -04:00
**By**: Claude Code Agent (Claude Opus)

**Branch**: feature/issue-78 at `f236df3`
**Addressed**: `## Local Review (Pre-Push)` (2026-09-15 10:35 -04:00, branch at `a6d3803`) — 2 must-fix, 7 suggestions
**Commits**: `b8c011d`, `b26e8af`, `2aeb718`, `51e38a0`, `f2712d1`, `1e678bc`, `2fc4ff5`, `f236df3`
**Tests**: `Summary: 80 tests, 0 errors, 0 failures, 0 skipped` (was 76; ament flake8 + pep257 clean)

### Actions
- [x] (must-fix) `buffer_dropped_bytes` now counts the resync discard as well as the trim, so it matches the ABC docstring's "how much of the stream was lost" and the node's WARN text — `parsers.py:_resync`; pinned by `test_aml_dropped_bytes_counts_the_resync_discard_too` (1000 garbage bytes then `99\r\r\n` at a 256 B cap: 1003 B, of which 1002 payload + the CR that ended the damaged sentence) — `b8c011d`
- [x] (must-fix) The README deferral now points at a list that carries the items: rolker/marine_tools#88's ask list names `parser_max_buffer_bytes`, `buffer_dropped_bytes` and `buffer_trim_count` (host-filed comment, linked from the plan) — `.agent/work-plans/issue-78/plan.md:228` — `b26e8af`
- [x] (suggestion) `parser_max_buffer_bytes` declared with a `read_only=True` `ParameterDescriptor` (plus a description naming the floor), so a field `ros2 param set` is rejected instead of silently ignored; `rcl_interfaces` added to `package.xml`; test `test_parser_max_buffer_bytes_is_read_only` — `node.py:78` — `2aeb718`
- [x] (suggestion) WARN back-off quiet-period reset re-anchored to the last **trim** observed (`_last_trim_seen_ns`), not the last WARN, matching the docstring; the reset test now asserts that a long gap since the last WARN alone does *not* reset — `node.py:_warn_on_buffer_trim` — `f2712d1`
- [x] (suggestion) `_publish_diagnostics` snapshots the correlated counter pair once and uses that snapshot for both the WARN text and the KeyValues; the "GIL-atomic" comment corrected to say each *individual* read is atomic, not the pair; test `test_trim_counters_are_snapshotted_once_per_tick` proves exactly one read of each per tick — `node.py:128` — `f2712d1`
- [x] (suggestion) (deferred: pre-existing defect, untouched by this change and out of #78's scope; the host is filing a follow-up beside rolker/marine_tools#90) Serial-thread death on a `nan`/`inf`/`snan` sentence: `int(decimal_value * 1000)` sits outside the `try` in `AMLParser._parse` — `sound_speed_bridge/sound_speed_bridge/parsers.py:253` (now `:262` after this round) — so the `ValueError`/`OverflowError` escapes `_serial_loop`'s `(SerialException, OSError)` catch and kills the daemon thread with `_serial_connected` still True
- [x] (suggestion) `main()` now constructs the node inside the `try`, catching a parameter `ValueError` into one FATAL line naming the parameter and still calling `rclpy.shutdown()`; the fail-loud intent is documented on the parameter declaration; test `test_main_reports_a_rejected_parameter_and_shuts_down` — `node.py:415` — `1e678bc`
- [x] (suggestion) `launch/aml_svs.launch.py` gains a `parser_max_buffer_bytes` launch argument at its 4096 default (typed `int` via `ParameterValue`) with a one-line comment — `2fc4ff5`
- [x] (suggestion) Plan's Files-to-Change attribute name corrected to `_last_warned_dropped_bytes` — `.agent/work-plans/issue-78/plan.md` — `b26e8af`

### Notes
- `plan.md` synced with all five code-affecting fixes under a revision-3 note
  and `[PR-R1-*]` markers (`f236df3`).
- One extra commit, `51e38a0`, fixes an `ament_pep257` D301 on the new test
  docstring; the `rcl_interfaces` import order (flake8 I100) is folded into
  `f2712d1`. Both linters are clean.
- Nothing pushed; no PR opened; no issues filed (per host instruction).

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 11:12 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: changes-requested

**Branch**: feature/issue-78 at `e2da2c4`
**Mode**: pre-push
**Depth**: Deep (round-1 tier), scoped to a bounded re-check of the eight fix commits
**Must-fix**: 1 | **Suggestions**: 3
**Round**: 2 | **Ship**: recommended — must-fix down 2 -> 1 and not rising; the one left is a one-line exit-status fix, not a design question. Address it, then push.

Specialists: Static Analysis (the package's own ament_flake8 + ament_pep257 tests, clean),
one Claude Adversarial pass (sonnet). Copilot and local-model passes off (not opted in).
Tests: `80 passed, 16 warnings in 1.55s` (pytest on an out-of-tree copy; was 76 at round 1).
Mutation check: 7 targeted mutations on an out-of-tree copy (resync accounting removed;
off-by-terminator in the resync count; `read_only=False`; reset re-anchored to the last WARN;
`_last_trim_seen_ns` never updated; KeyValues un-snapshotted; node constructed outside
main()'s try) — all 7 killed by the new tests.

Round-1 must-fixes verified closed:
- MF1 (`buffer_dropped_bytes` undercount): closed. The review's concrete case replays to
  744 trimmed + 259 resync-discarded = 1003. A byte-conservation sweep over stall,
  stall+recover, crlf-regex and healthy streams shows zero unaccounted payload bytes — the
  only uncounted bytes are AML's 2 B/sentence CRCRLF padding, which appears identically on a
  healthy stream, so it is framing artifact and not stream loss. `_resync` and `_trim_residue`
  are the only buffer-shrinking sites and never overlap within one `feed()`, so no double count.
- MF2 (README deferral): closed. rolker/marine_tools#88's comment names all three items
  (`parser_max_buffer_bytes`, `buffer_dropped_bytes`, `buffer_trim_count`) — read directly.

Other bounded checks, all clean: the `read_only=True` descriptor does not break the launch
file's typed default (`aml_svs.launch.py` evaluates `parser_max_buffer_bytes = 4096 (int)`;
a real node built with `-p parser_max_buffer_bytes:=8192` gets 8192 in both the parameter and
the parser), and `test_parser_max_buffer_bytes_is_read_only` genuinely exercises a rejected
set (with `read_only=False` the set succeeds and the assert fails). First-trim WARN latency and
the doubling-to-300 s spam bound are unchanged by the re-anchoring. `rcl_interfaces` is a
correct and required `<depend>`. Plan drift: the revision-3 sync is honest; only `package.xml`
is missing from Files-to-Change.

### Findings
- [ ] (must-fix) A refused `parser_max_buffer_bytes` now exits **0** — verified by running `main()` with `-p parser_max_buffer_bytes:=128`: FATAL logged, exit status 0, where before `1e678bc` the uncaught ValueError exited 1. `ros2 launch` reports "process has finished cleanly" and systemd `Restart=on-failure` / `OnProcessExit` failure handlers never fire, so a node that refused to start is indistinguishable from a clean shutdown to everything but a human reading the log — contradicting the change's own "the node still refuses to start -- that is deliberate". One line: `raise SystemExit(1)` after the FATAL — `sound_speed_bridge/sound_speed_bridge/node.py:456`
- [ ] (suggestion) `except ValueError` spans `rclpy.spin(node)`, not just construction: patching `spin` to raise ValueError produces `sound_speed_bridge started: ...` followed by `failed to start: ...`. Narrow the try to the constructor, or word the message off `node is None` — `sound_speed_bridge/sound_speed_bridge/node.py:456`
- [ ] (suggestion) `test_main_reports_a_rejected_parameter_and_shuts_down` restores the rclpy context with an unguarded `rclpy.init()`; an assertion failure before that line adds a confusing teardown ERROR on top of the real failure (verified). Wrap in `try/finally` — `sound_speed_bridge/test/test_node.py:218`
- [ ] (suggestion) Plan's Files-to-Change gained the launch-file row but not `sound_speed_bridge/package.xml` (the `rcl_interfaces` depend), and the `test_node.py` row does not name the three new tests — cosmetic — `.agent/work-plans/issue-78/plan.md`
