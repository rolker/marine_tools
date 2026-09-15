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

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 10:57 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: approved

**Branch**: feature/issue-78 at `83f318f`
**Mode**: pre-push
**Depth**: Deep (round-1 tier), scoped to a bounded re-check of commit `83f318f` only
**Must-fix**: 0 | **Suggestions**: 3
**Round**: 3 | **Ship**: recommended — the round-2 must-fix is closed and pinned by a test; nothing new in scope. The three suggestions are one deferred pre-existing repo-wide defect, one missing regression test for a suggestion-level fix, and plan cosmetics.

Specialists: Static Analysis (the package's own `ament_flake8` + `ament_pep257` tests,
both pass; a direct `flake8` run on `test/test_node.py` shows only ament-ignored
I1xx/I2xx/B902 codes). No adversarial fan-out — the scope is one commit and every
claim below was verified by execution. Copilot and local-model passes off (not opted in).
Tests: `80 passed, 16 warnings in 1.19s` (unchanged count from round 2; ament lint tests
included).

Round-2 must-fix verified closed, by execution:
- A refused `parser_max_buffer_bytes` now exits **1**. Running the console-script entry
  point (`sound_speed_bridge.node:main`) with `-p parser_max_buffer_bytes:=128` gives
  exit status 1, exactly one `[FATAL] ... failed to start: parser_max_buffer_bytes must
  be an integer >= 256 ...` line, **zero** traceback lines, and `rclpy.ok()` False
  afterwards. Mutation: deleting `raise SystemExit(1) from exc` is **killed** by
  `test_main_reports_a_rejected_parameter_and_shuts_down`.
- `SystemExit` raised inside `main()` still runs the `finally`: with `spin` patched to
  raise `SystemExit(7)` the code propagates unchanged, `destroy_node()` ran once and
  `rclpy.ok()` is False. `SystemExit` is not an `Exception`, so the outer
  `except KeyboardInterrupt` cannot swallow it, and setuptools' `sys.exit(main())`
  wrapper carries the code to the process status.
- Round-2 suggestion 1 (except scoped to construction): a `ValueError` raised from
  `spin()` propagates out of `main()` with **0** `logger.fatal` calls — no longer
  mislabelled a start failure.
- Round-2 suggestion 2 (test context restore): with an assertion failure injected before
  the restore, the run reports one clean `AssertionError` and no teardown ERROR.
- Round-2 suggestion 3 (plan `package.xml` row): present at `plan.md:338`.
- Healthy construction still spins: a real `rclpy.spin` on a mocked serial port runs, and
  a `KeyboardInterrupt` out of `spin()` returns from `main()` normally with
  `destroy_node()` called once and the context shut down.

### Findings
- [ ] (suggestion) (deferred: pre-existing and repo-wide, untouched by this change) A real SIGINT stop exits **1** with an `RCLError: failed to shutdown: rcl_shutdown already called on the given context` traceback: rclpy's own signal handler shuts the context down before `finally:` reaches `rclpy.shutdown()`. Verified by execution and reproduced identically on `origin/jazzy`, and `zda_serial_bridge`, `kongsberg_em_bridge` and `garmin_sidescan` share the unguarded pattern. It sits directly on this commit's contract — under `Restart=on-failure` a deliberate stop is indistinguishable from the refusal this commit just made exit 1. Fix is `rclpy.try_shutdown()` (or an `if rclpy.ok():` guard); file a follow-up rather than widening #78 — `sound_speed_bridge/sound_speed_bridge/node.py:467`
- [ ] (suggestion) Nothing pins the narrowed `except` scope: mutating `main()` back to a single `except ValueError` spanning `spin()` **survives** the whole `test_node.py` suite (11 passed). A spin-time `ValueError` would again be logged `failed to start` and converted to exit 1 with no test noticing. A three-line test patching `rclpy.spin` to raise `ValueError` and asserting `logger.fatal` was not called would close it — `sound_speed_bridge/test/test_node.py`
- [ ] (suggestion) Plan cosmetics, the residue of round-2 suggestion 3: the header's revision list stops at Revision 3 while the Files-to-Change table now carries `[PR-R2-MF1]`/`[PR-R2-S1]`/`[PR-R2-S3]` markers that no revision note defines, and the `test_node.py` row still does not name the three new tests — `.agent/work-plans/issue-78/plan.md:7`

## Implementation
**Status**: complete
**When**: 2026-09-15 11:35 -04:00
**By**: Claude Code Agent (Claude Opus)

**Branch**: feature/issue-78 at `23c5267`
**Addressed**: the operator's publish-gate decision on the two pre-existing
defects raised by `## Local Review (Pre-Push)` round 1 (suggestion 6) and
round 3 (suggestions 1 and 2) — verbatim: *"Fix those before publishing to
reduce issue churn and get fixes done quicker."* Both are therefore fixed
on this branch instead of being filed as follow-ups.
**Commits**: `5937b6e`, `e6e1888`, `23c5267`
**Tests** (all four touched packages, `./sensors_ws/build.sh` then
`./sensors_ws/test.sh`; ament flake8 + pep257 included in each and clean):

- sound_speed_bridge — `Summary: 103 tests, 0 errors, 0 failures, 0 skipped` (was 80)
- zda_serial_bridge — `Summary: 44 tests, 0 errors, 0 failures, 0 skipped` (was 43)
- kongsberg_em_bridge — `Summary: 56 tests, 0 errors, 0 failures, 0 skipped` (was 55)
- garmin_sidescan — `Summary: 82 tests, 0 errors, 0 failures, 0 skipped` (was 81)

### Actions
- [x] (defect 1 — SW1) A non-finite sentence no longer kills the serial reader thread. `AMLParser._parse` converted the Decimal to integer mm/s *outside* its `try`, so `nan`/`inf`/`-inf`/`snan` (all valid Decimal literals) or an exponent that overflows a float (`1e999`) raised `InvalidOperation`/`OverflowError`/`ValueError` out of `feed()`, past `_serial_loop`'s `(SerialException, OSError)` catch, ending the daemon thread with `_serial_connected` still True — a node reporting a healthy link that never publishes again. Both conversions now sit inside the guarded region and the result is checked for finiteness; a sentence that cannot yield a finite number is a parse failure like any other (NaN, `raw_bytes` preserved, counted by the existing parse-error path) — `sound_speed_bridge/sound_speed_bridge/parsers.py` — `5937b6e`
- [x] (defect 1 — SW1) `RegexParser` checked for the same class of hazard, as the review asked, and it had it one step downstream: `float()` accepts `'nan'`/`'inf'` and overflows `'1e999'`, so a loose pattern published an **infinite** sound speed — and `format_valeport`/`format_template` `round()` it *on the serial thread*, raising `OverflowError` past the same catch. A non-finite value (including one produced by `sound_speed_scale`) is now NaN. `_optional_float` follows the same rule, so an infinite temperature/pressure is an unreported field rather than `inf` in a `Temperature`/`FluidPressure` message. The diagnostics comparisons were checked too: a NaN last reading is already a WARN state there, so no change was needed — `5937b6e`
- [x] (defect 2 — SW2) A deliberate SIGINT stop now exits 0 in all four nodes. rclpy's own signal handler shuts the context down before `main()` sees anything, so `spin()` raised an uncaught `ExternalShutdownException` **and** the `finally`'s `rclpy.shutdown()` raised `RCLError: rcl_shutdown already called`: exit 1, two tracebacks, indistinguishable from a crash under `Restart=on-failure`. Each `main()` now catches `ExternalShutdownException` alongside `KeyboardInterrupt` and shuts down through the idempotent `rclpy.try_shutdown()`; `kongsberg_em_bridge`'s `if rclpy.ok(): rclpy.shutdown()` becomes `try_shutdown()` too (same intent, no check-then-act race). `destroy_node()` is left unguarded — verified by execution to run correctly on an already-shut-down context. `sound_speed_bridge` keeps its `SystemExit(1)` on a refused parameter and the `except` scoped to construction — `sound_speed_bridge`, `zda_serial_bridge`, `kongsberg_em_bridge`, `garmin_sidescan` `node.py` — `e6e1888`
- [x] (round-3 suggestion 2) `test_a_valueerror_from_spin_is_not_reported_as_a_start_failure` pins the narrowed `except` scope: a `ValueError` out of `spin()` must propagate with **no** `logger.fatal` call. Mutating `main()` back to one `except ValueError` spanning `spin()` fails it — `sound_speed_bridge/test/test_node.py` — `e6e1888`
- [x] (round-3 suggestion 3) Plan cosmetics: revision 4 defines the `[PR-R2-*]`/`[PR-R3-*]` markers the Files-to-Change table already carried, and the `test_node.py` row now names every test it contributes — `.agent/work-plans/issue-78/plan.md` — `23c5267`
- [x] Scope widening recorded in the plan (Scope widening section with the operator quote, Files to Change, Consequences) — `23c5267`

### Verification
- **SIGINT, by execution** (console entry points, serial/IO mocked, real `SIGINT` to the process): `sound_speed_bridge` **exit 0, 0 traceback lines** (before: exit 1 with `ExternalShutdownException` + `RCLError: rcl_shutdown already called`); `zda_serial_bridge` **exit 0, 0 traceback lines**; `kongsberg_em_bridge` **exit 0, 0 traceback lines**.
- **`garmin_sidescan` still exits 1 on SIGINT in that bench run — for a different, third defect.** With no sonar present its `_reconcile_transmit_param` **timer callback** calls `set_parameters()` after the signal handler has shut the context down, and the resulting `RCLError: Failed to publish: publisher's context is invalid` propagates out of `spin()`. That is a callback-versus-shutdown race inside the node, not the `main()` contract fixed here, and it predates this branch (`main()`'s two lines are now correct, and its `main()`-level test passes). Surfaced to the operator rather than fixed: the publish-gate decision widened scope to the two defects the reviews found, and this is a third.
- **Mutation checks** (out-of-tree copies only): 6 mutations, all killed. (1) the exact pre-fix `AMLParser._parse`, with `int()` outside the `try` — killed by 10 tests including the node-level thread-survival test; (2) finiteness check dropped from the AML guard; (3) dropped from `RegexParser._parse`; (4) dropped from `_optional_float`; (5) `try_shutdown()` reverted to `shutdown()` — killed by the real-SIGINT test and the in-process one; (6) `ExternalShutdownException` removed from the `except` — killed by the in-process test; plus (7) `except ValueError` widened back over `spin()` — killed by the new scope test.
- No `.agents/README.md` exists in this repo (root or per package), so there was no parameter/topic table to update; `sound_speed_bridge`'s README is still deferred to rolker/marine_tools#88.

### Notes
- Tests added: parser-level non-finite cases on both parsers (parametrized over `nan`/`NaN`/`inf`/`-inf`/`Infinity`/`snan`/`1e999`/`-1e999`, plus a scale-product overflow and the optional fields), a node-level test that drives the **real** `_serial_loop` over `nan`/`inf`/`1e999` sentences and asserts the thread is alive and the next good sentence publishes, a real-SIGINT subprocess test for `sound_speed_bridge` and `zda_serial_bridge`, an in-process `ExternalShutdownException` test for `sound_speed_bridge`, `kongsberg_em_bridge` and `garmin_sidescan` (the latter two as new `test/test_main_shutdown.py` files, node mocked so only `main()` is under test), and the except-scope test above.
- `garmin_sidescan` needs `marine_radar_control_msgs` built first; `colcon build --packages-up-to` covers it in this worktree.
- Nothing pushed; no PR opened; no issues filed (per host instruction).

## Implementation
**Status**: complete
**When**: 2026-09-15 11:49 -04:00
**By**: Claude Code Agent (Claude Opus)

**Branch**: feature/issue-78 at `6e797d4`
**Addressed**: the third defect the previous pass surfaced and left out of
scope — `garmin_sidescan` still exiting 1 on a deliberate SIGINT — now in
scope under the operator's standing publish-gate decision, verbatim:
*"Fix those before publishing to reduce issue churn and get fixes done
quicker."*
**Commits**: `ac7803a`, `6e797d4`
**Tests**: `garmin_sidescan` — `Summary: 86 tests, 0 errors, 0 failures, 0 skipped`
(was 82; `colcon test-result --test-result-base build/garmin_sidescan`. The
layer-wide line `./sensors_ws/test.sh` prints, `Summary: 289 tests, 0 errors,
0 failures, 0 skipped`, covers every package already built in the worktree.)
ament flake8 + pep257 are inside that count and clean.

### Actions
- [x] (defect 3 — SW3) A callback can no longer fail the process at shutdown.
  rclpy's signal handler shuts the context down while the executor is still
  inside `spin()`, so `_reconcile_transmit_param` reached `set_parameters()`
  — and the parameter-event publish inside it — on a dead context:
  `RCLError: Failed to publish: publisher's context is invalid` propagated
  out of `spin()`, so a clean operator stop was exit 1 with a traceback,
  which is exactly the signal #78's exit-code contract is trying to keep
  meaningful under `Restart=on-failure`. A `quiet_on_shutdown` decorator now
  guards the **call** — a bare `if rclpy.ok()` before it would be
  check-then-act and the shutdown can land in the gap — catching
  `RCLError`/`InvalidHandle` and consulting `rclpy.ok(context=self.context)`
  only afterwards: a shutdown in flight returns quietly, a failure on a live
  context is re-raised unchanged. Applied to the three timer callbacks, the
  `~/change_state` subscription callback, `_publish_tx_state` /
  `_publish_control_set` (which the startup daemon thread and the
  `~/set_transmit` service reach) and the `_rx_loop` / `_aux_loop` thread
  entries, where a stray `RCLError` at shutdown would otherwise kill a daemon
  thread with a traceback on stderr. Normal behaviour is untouched —
  `garmin_sidescan/garmin_sidescan/node.py` — `ac7803a`
- [x] Four tests drive the callbacks against a **real** shut-down `rclpy`
  `Context` (not a mock of `rclpy.ok`), raising the exact `RCLError` the field
  shows: quiet on a dead context for the reconcile callback and for a sibling
  publishing timer; still raised on a **live** context; and a non-RCL bug in a
  callback body still surfaces — `garmin_sidescan/test/test_main_shutdown.py`
  — `ac7803a`
- [x] Plan: [SW3] moved out of Out of scope into Scope widening with the
  operator quote, plus revision note, Files to Change and Consequences rows —
  `.agent/work-plans/issue-78/plan.md` — `6e797d4`

### Verification
- **Deterministic, by execution, on the console entry point** (I/O mocked,
  nothing about the failing call mocked): with the context shutdown forced to
  land *inside* the reconcile callback, the pre-fix code exits **1** with
  `rclpy._rclpy_pybind11.RCLError: Failed to publish: publisher's context is
  invalid, at ./src/rcl/publisher.c:423`; the fixed code exits **0** with **0**
  traceback lines. This is the discriminating check — see the caveat below.
- **Real SIGINT to the console entry point** (I/O mocked, real signal):
  `garmin_sidescan` exits **0**, **0** traceback lines, no `RCLError`.
  **Caveat, stated because it bounds what that run proves**: on this bench the
  *pre-fix* code also exits 0 under a plain real-SIGINT run. The failure is a
  race — the callback has to be dispatched, or be mid-call, exactly across the
  context teardown — and it did not land here in repeated attempts (5 runs of a
  high-rate reconcile timer with a standing disagreement, 3 plain runs). The
  previous pass observed it landing. So the real-SIGINT run confirms no
  regression, and the forced-ordering run above is what proves the fix.
- **Mutation checks** (out-of-tree copies only, run against the committed
  tests): 4 mutations, all killed. (1) both decorators stripped — the exact
  pre-fix code — killed by the two quiet-on-shutdown tests; (2) blanket swallow
  (`return None` with no `rclpy.ok` re-raise) — killed by the live-context
  test; (3) `except Exception` instead of `(RCLError, InvalidHandle)` — killed
  by the non-RCL test; (4) check-then-act (`if not rclpy.ok(): return` before
  the call, no try) — killed by two tests, which is the point: it changes
  semantics as well as being racy.
- `rclpy.exceptions` does not export `RCLError`; it is
  `rclpy.impl.implementation_singleton.rclpy_implementation.RCLError`, a
  `RuntimeError` subclass, imported the way rclpy's own code imports it.
- No `.agents/README.md` exists in this repo, so there was no parameter/topic
  table to update. No topic, service, parameter or launch change.

### Notes
- **Residual, surfaced not assumed away**: `sound_speed_bridge`,
  `zda_serial_bridge` and `kongsberg_em_bridge` each run a publishing timer
  (`_publish_diagnostics`, `_sonar_info_heartbeat`) and so share this race in
  principle. It has never been observed on them and their real-SIGINT runs
  exit 0. Extending the guard to them is a **fourth** scope widening the
  operator has not been asked for, so it was not done — it is the operator's
  one-line yes/no, recorded in the plan's Consequences table.
- Nothing pushed; no PR opened; no issues filed (per host instruction).

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 11:58 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: changes-requested

**Branch**: feature/issue-78 at `05f6df5`
**Mode**: pre-push
**Depth**: Deep (reason: ~930 lines across 13 files and 4 packages; concurrency + lifecycle + shutdown paths), scoped to the commits since round 3 — `5937b6e`, `e6e1888`, `ac7803a` and the plan commits `23c5267` / `6e797d4`
**Must-fix**: 1 | **Suggestions**: 4
**Round**: 4 | **Ship**: recommended — the single must-fix is one sentence of plan prose; every code finding this round is pre-existing, narrow, and outside the contract the three commits set out to restore.

Specialists: Static Analysis (each package's own `ament_flake8` + `ament_pep257` tests — inside the passing counts below, all clean); Governance; Plan Drift; two fresh-context Claude Adversarial passes (Lens A on the finiteness change, Lens B on the `quiet_on_shutdown` decorator and the four `main()`s), both `model: sonnet`. Copilot and local-model passes off (not opted in).

**Tests** — `./sensors_ws/build.sh` clean (`Summary: 4 packages finished`), then `./sensors_ws/test.sh sound_speed_bridge zda_serial_bridge kongsberg_em_bridge garmin_sidescan`:

- layer-wide: `Summary: 289 tests, 0 errors, 0 failures, 0 skipped`
- sound_speed_bridge: `Summary: 103 tests, 0 errors, 0 failures, 0 skipped`
- zda_serial_bridge: `Summary: 44 tests, 0 errors, 0 failures, 0 skipped`
- kongsberg_em_bridge: `Summary: 56 tests, 0 errors, 0 failures, 0 skipped`
- garmin_sidescan: `Summary: 86 tests, 0 errors, 0 failures, 0 skipped`

Every per-package count matches the `## Implementation` entries' claims exactly.

Verified by execution (this review, not taken on trust):

- **The node-level thread-survival test is real.** An out-of-tree copy with `AMLParser._parse` reverted to the exact pre-fix form (`git show 5937b6e^`) fails `test_serial_thread_survives_a_non_finite_sentence` with `ValueError: cannot convert NaN to integer` out of the daemon thread, caught by the `is_alive()` assertion. The mutation is killed by *that* test, not incidentally by others.
- **The finiteness change has no bad downstream interaction.** `format_valeport` and `format_template` both already skipped NaN before this branch (`sinks.py` is untouched), so no NaN reaches `round()` or a UDP datagram. `raw_mm_s` is consumed *only* in `sinks.py`; `None` is already a supported value there and `format_valeport`'s existing `mm_s > 9999999` bound rejects a finite-but-enormous one. Nothing in the path is a fixed-width field: `SoundSpeed.sound_speed`, `Temperature.temperature` and `FluidPressure.fluid_pressure` are all `float64` and accept NaN and `1e300` without raising, so `_optional_float` returning `None` changes no published field's type — it only suppresses that optional publish, which is the node's pre-existing `is not None` pattern. `_publish_diagnostics` tests `math.isnan` *before* its range comparisons, so a NaN reading does not read as OK.
- **The Decimal/float edge set behaves as the code assumes**: `Decimal('snan')` → `float()` raises `ValueError` (in the widened except), `float(Decimal('1e999'))` returns `inf` rather than raising, `nan`/`inf`/`-inf`/`Infinity` all convert to non-finite floats and are caught by `math.isfinite`.
- **Import paths are correct on Jazzy**: `rclpy.executors.ExternalShutdownException` exists and subclasses `Exception`; `rclpy.try_shutdown` exists; `RCLError` is *not* in `rclpy.exceptions` (the `rclpy.impl.implementation_singleton` route the code uses is the right one) and both it and `InvalidHandle` are `RuntimeError` subclasses.
- **Test order-independence**: all four packages pass with their test files in reversed order, and `garmin_sidescan/test/test_main_shutdown.py` passes when collected twice in one process. No context leaks between tests; the new per-`Context` tests never touch the default context, and the `main()` tests restore it in a `finally`.
- **The decorator does cover the SIGINT thread window.** Running the `garmin_sidescan` console entry point under a real SIGINT with a spy on `destroy_node` shows `rclpy.ok()=False` and `context.ok()=False` on entry to `destroy_node()`, exit 0 — so on the path SW3 targets, an in-flight thread publish hits the swallow branch, not the re-raise branch. This is what downgraded an adversarial must-fix to suggestion 1 below.
- The service path is unaffected: `_on_set_transmit` is undecorated and its `success`/`message` come from `self._transmitting`, set by the *undecorated* `_send()`, so swallowing a mirror publish cannot hand an operator a false success. The safety-critical shutdown transmit-OFF in `destroy_node()` goes over the raw socket, not a publisher, so it is outside the decorator entirely.

Governance: no parameter, topic, service, message or launch change in these three commits, so no README consequence (`sound_speed_bridge` has no README — deferred to rolker/marine_tools#88; `garmin_sidescan`'s README documents behaviour these commits do not alter). No `.agents/README.md` exists in this repo. All commits carry the agent identity; nothing pushed; no issues filed.

### Findings
- [ ] (must-fix) The plan asserts an operator action that the record does not show: "[SW3] ... was carried back to the operator, **who applied the same standing decision to it**". The only recorded operator statement is the round-3 publish-gate quote, given about [SW1]/[SW2]; the `## Implementation` entry of 11:35 says SW3 was "surfaced to the operator rather than fixed", and the 11:49 entry re-quotes that same earlier sentence rather than a new go-ahead. AGENTS.md § Documentation Accuracy forbids attributing a decision to someone who did not state it, and the workspace rule that a go-ahead answers only the question asked is the reason it matters here. Fix is one sentence: either quote the operator's actual SW3 approval, or say plainly that the agent applied the standing decision and the confirmation is owed — `.agent/work-plans/issue-78/plan.md` (Scope widening intro, and the same wording in the Revision 5 note)
- [ ] (suggestion) `garmin_sidescan`'s `destroy_node()` sets `self._running = False` and calls `super().destroy_node()` without joining `_rx_thread` or the two `_aux_loop` threads, so a thread already past its `while self._running` check can publish into a publisher being torn down. On the SIGINT path this is covered (verified above: the context is already down, so the decorator swallows), but on a **non-signal** teardown the context is still live and `quiet_on_shutdown` re-raises — a traceback on stderr, though a daemon thread cannot change the exit code. Pre-existing (predates this branch) and `sound_speed_bridge` already has the pattern to copy (`node.py:430-437`, `join(timeout=2.0)`). Fixing it is a **fourth** scope widening, so it is the operator's one-line yes/no — it belongs in the plan's Consequences residual row beside the existing one, not silently done — `garmin_sidescan/garmin_sidescan/node.py:1085`
- [ ] (suggestion) `quiet_on_shutdown` decides "shutdown in flight" vs "genuine fault" from `rclpy.ok()` *after* the fact, not from what raised, so an unrelated real `RCLError` that happens to coincide with a shutdown is swallowed. That is the right trade, but the docstring states it more strongly than it holds ("a failure on a live context is re-raised unchanged, so a genuine fault is still loud") — a one-line caveat would keep the doc honest — `garmin_sidescan/garmin_sidescan/node.py:79`
- [ ] (suggestion) `sound_speed_bridge` and `zda_serial_bridge` each got a real-SIGINT subprocess guard, but `kongsberg_em_bridge` and `garmin_sidescan` have only the in-process `ExternalShutdownException` test — and `garmin_sidescan` is the node where the shutdown race was actually observed. The SIGINT run does not discriminate the SW3 fix (stated in the implementation entry, correctly), but it would pin the SW2 exit-code contract against future regression in those two packages for ~10 lines — `kongsberg_em_bridge/test/test_main_shutdown.py`, `garmin_sidescan/test/test_main_shutdown.py`
- [ ] (suggestion) `e6e1888` bundles two logical changes: the four-package SW2 shutdown fix and the round-3 suggestion-2 regression test `test_a_valueerror_from_spin_is_not_reported_as_a_start_failure`, which pins the *round-2* except-scope fix and has nothing to do with the shutdown contract. Cosmetic against AGENTS.md's atomic-commit rule; not worth a rewrite this late — noted so it is not repeated

## Integrated Review
**Status**: complete
**When**: 2026-09-15 12:24 -04:00
**By**: Claude Code Agent (Claude Fable 5.1)

**PR**: #91 at `ff6b62f`
**Sources**: 3 (Copilot R1 @ `ff6b62f` — 2 inline + 1 suppressed; Local Review (Pre-Push) rounds 1–4; CI rollup). Copilot's first attempt @ `bbaf25c` errored with no content.
**Cross-source confirmations**: 0
**CI**: all-pass (build-and-test, copilot-pull-request-reviewer)

### Findings
- [x] (must-fix, Copilot) finite-but-huge capture (`1e306`) passes the parser's finiteness check, then `round(value * 1000)` in the Valeport/template formatter overflows on the serial thread — reject a non-finite mm/s product at the parser and guard both formatters — `parsers.py:389`, `sinks.py` — fixed inline, tests on parser + both formatters
- [x] (low, Copilot) PR body still asked for confirmation of the [SW3] garmin widening after the plan recorded the operator's "garmin fix is ok" — PR body updated to match
- [x] (low, Copilot suppressed) floor docstring claimed 256 B is the longest legitimate sentence for any configured parser; `regex_pattern` bounds nothing — reworded: the floor is the read size, the cap must exceed the configured protocol's longest line — `parsers.py:118`

### False positives
- none.

## Integrated Review
**Status**: complete
**When**: 2026-09-15 12:43 -04:00
**By**: Claude Code Agent (Claude Fable 5.1)

**PR**: #91 at `8f4bd88` (jazzy with PR #89 merged in)
**Sources**: 2 (Copilot R2 @ `8f4bd88` — 1 inline + 2 suppressed; CI rollup)
**Cross-source confirmations**: 0
**CI**: all-pass

### Findings
- [x] (low, Copilot) `parser_max_buffer_bytes` description still called 256 B "the longest legitimate sentence" — reworded as the read-size floor with sizing guidance — `node.py:103`
- [x] (must-fix, Copilot suppressed) `format_template` no longer skipped a NaN reading when `raw_mm_s` was present — unconditional NaN check restored, regression test with `raw_mm_s` populated — `sinks.py:104`
- [x] (low, Copilot suppressed) plan floor rationale repeated the overstated claim — `plan.md:258`
- [x] (low, host-found from Copilot's "generated-log cleanup") a colcon `log/` tree had been committed by an in-repo test run — removed, `log/` ignored

### False positives
- none.

## Integrated Review
**Status**: complete
**When**: 2026-09-15 12:52 -04:00
**By**: Claude Code Agent (Claude Fable 5.1)

**PR**: #91 at `5e1e580`
**Sources**: 3 (Copilot R3 @ `5e1e580` — 2 inline + 6 suppressed; Local Review (Pre-Push) round 4 residuals; CI rollup)
**Cross-source confirmations**: 1
**CI**: all-pass

### Findings
- [x] (cross-confirmed: Copilot suppressed ×2 + Local Review round 4 residual) `_publish_diagnostics` is an unguarded timer in `sound_speed_bridge` and `zda_serial_bridge` (and `kongsberg_em_bridge`'s heartbeat), so SIGINT during a publish lets `RCLError` escape `spin()` before the new handler — the garmin `quiet_on_shutdown` class of fix, applied to the other nodes — `sound_speed_bridge/node.py:797`, `zda_serial_bridge/node.py:500`
- [x] (must-fix, Copilot ×2 inline) AML path: `1e306` is finite as m/s but its mm/s product is inf; `raw_mm_s` is populated so `format_template` bypasses the product guard and can interpolate inf — reject a non-finite mm/s product in `AMLParser._parse` and validate the product unconditionally in both formatters before choosing the raw path; regression with an AML `1e306` sentence and a populated-`raw_mm_s` reading — `parsers.py:289`, `sinks.py:104`
- [x] (should-fix, Copilot suppressed) counter pair (`buffer_dropped_bytes`, `buffer_trim_count`) updated in separate statements on the serial thread; the timer can read a torn pair — expose an atomic snapshot (single tuple assignment) from the parser — `node.py:705`, `parsers.py`
- [x] (low, Copilot suppressed ×3) floor wording "the longest legitimate sentence" still in the node FATAL text, the parser ValueError text, and the DEFAULT docstring — `node.py:262`, `parsers.py:143`, `parsers.py:113`

### False positives
- none.

## Implementation
**Status**: complete
**When**: 2026-09-15 13:09 -04:00
**By**: Claude Code Agent (Claude Opus)

**PR**: #91 at `db96536` (branch `feature/issue-78`; not pushed by this pass)
**Addressed**: `## Integrated Review` of 2026-09-15 12:52 (Copilot R3 @ `5e1e580`
+ Local Review round-4 residuals) — all four open findings, each on the host's
explicit decision to fix rather than defer.
**Commits**: `90822a8`, `e2b6d5b`, `36989be`, `6efd0be`, `db96536`
**Tests** — `./sensors_ws/build.sh` clean (`Summary: 4 packages finished`), then
`./sensors_ws/test.sh sound_speed_bridge zda_serial_bridge kongsberg_em_bridge
garmin_sidescan`; per-package counts from
`colcon test-result --test-result-base sensors_ws/build/<pkg>`. ament flake8 +
pep257 are inside each count and clean.

- layer-wide: `Summary: 329 tests, 0 errors, 0 failures, 0 skipped`
- sound_speed_bridge: `Summary: 137 tests, 0 errors, 0 failures, 0 skipped` (was 103)
- zda_serial_bridge: `Summary: 47 tests, 0 errors, 0 failures, 0 skipped` (was 44)
- kongsberg_em_bridge: `Summary: 59 tests, 0 errors, 0 failures, 0 skipped` (was 56)
- garmin_sidescan: `Summary: 86 tests, 0 errors, 0 failures, 0 skipped` (unchanged)

### Actions
- [x] (must-fix, Copilot ×2 inline) The finite-in-m/s, infinite-in-mm/s hole is
  closed at both ends. `AMLParser._parse` now tests `math.isfinite(parsed * 1000.0)`
  as well as the value, matching `RegexParser`: `1e306` m/s is a perfectly finite
  float, so the old value-only test passed it through *with* an exact
  Decimal-derived `raw_mm_s`. Both formatters now compute and validate the mm/s
  product **unconditionally, before** choosing the raw-integer path — the ordering
  is the fix, since `format_template` previously bypassed the guard entirely
  whenever `raw_mm_s` was populated and would have interpolated `inf` into
  `{value_mm_s}`, and `format_valeport` would have emitted from an unvalidated
  integer. Both happen on the serial thread, where `round(inf)`'s `OverflowError`
  escapes `_serial_loop`'s `(SerialException, OSError)` catch. Tests: an AML
  `1e306`/`-1e306`/`1e308` sentence parses to NaN with `raw_mm_s` None; the
  node-level real-`_serial_loop` test now replays a `1e306` sentence too and still
  asserts the thread is alive and the next good sentence publishes; and one sinks
  test drives both formatters with a huge/NaN value **and** a populated `raw_mm_s`
  — `sound_speed_bridge/sound_speed_bridge/parsers.py`, `sinks.py` — `90822a8`
- [x] (cross-confirmed: Copilot suppressed ×2 + Local Review round 4 residual)
  **[SW4]** The [SW3] callback-versus-shutdown guard is extended to the publishing
  timers of the other three nodes: `_publish_diagnostics` in `sound_speed_bridge`
  and `zda_serial_bridge`, `_sonar_info_heartbeat` in `kongsberg_em_bridge`. Each
  publish **call** is guarded (not preceded by an `if rclpy.ok()` check-then-act,
  which the shutdown can land inside), catching `RCLError`/`InvalidHandle` and
  consulting `rclpy.ok(context=self.context)` only afterwards: quiet on a dead
  context, re-raised unchanged on a live one. No copy of `garmin_sidescan`'s
  decorator machinery — that node has ten guarded call sites, these have one each,
  and the four packages share no Python package (`marine_tools` itself is
  `ament_cmake`/C++), so a shared helper would mean a new cross-package runtime
  dependency for five lines. `kongsberg_em_bridge`'s blanket `except Exception` is
  **narrowed, not removed**: the RCL class is now triaged against the context (a
  publisher that has stopped working mid-survey is not something a heartbeat should
  paper over), every other exception keeps its throttled warning. Three tests per
  node against a **real** shut-down `rclpy.Context` — not a mock of `rclpy.ok` —
  with the publish raising the exact rcl error the field shows: quiet on a dead
  context, still raised on a live one, and a non-RCL bug not swallowed (for
  kongsberg, still warned and not propagated) —
  `sound_speed_bridge/test/test_shutdown_guard.py`,
  `zda_serial_bridge/test/test_shutdown_guard.py`,
  `kongsberg_em_bridge/test/test_main_shutdown.py` — `e2b6d5b`
- [x] (should-fix, Copilot suppressed) The trim counters are one atomic pair.
  `buffer_dropped_bytes`/`buffer_trim_count` were two attributes written on the
  serial thread and read on the diagnostics timer, so a snapshot taken between the
  two writes of one trim reported a count without its bytes — and the node prints
  those two numbers into a WARN line and a `/diagnostics` KeyValue an operator is
  meant to correlate. They are now one tuple rebound in a single (GIL-atomic)
  assignment, exposed as a `trim_stats` snapshot the node reads once per tick; the
  two public names survive as read-only views. Tests: every rebind is a whole pair
  whose dropped-byte half moves forward (which rejects a two-step write in either
  order), the views agree with the snapshot and no longer accept assignment, and
  the existing once-per-tick test's parser stand-in now *raises* if either
  individual counter is touched — `parsers.py`, `node.py` — `36989be`, `db96536`
- [x] (low, Copilot suppressed ×3) The three operator-facing strings no longer
  claim 256 B is "the longest legitimate sentence": the node's startup FATAL text,
  the parser's `ValueError`, and the `DEFAULT_MAX_BUFFER_BYTES` docstring. Nothing
  bounds a `regex_pattern` line, so the claim was false in the one place an
  operator reads it while sizing the cap. Each now says what is true — 256 B is
  the serial read size, a sanity floor below which one healthy read chunk
  overflows the cap, and the cap itself must exceed the longest sentence of the
  configured protocol (AML ~11 B, BizzyBoat `$AML,SVM` ~32 B). The plan's matching
  rationale bullet is corrected too. No test asserted the old text —
  `node.py:~262`, `parsers.py:~113`, `~143` — `6efd0be`
- [x] Plan kept in sync: **[SW4]** recorded as a fourth scope widening (revision 6
  note, its own Scope-widening section, Files-to-Change rows, Consequences row),
  with the operator's standing quote and the Copilot cross-confirmation named as
  the basis, and the [SW3] row's "residual" language replaced since the residual is
  now fixed — `.agent/work-plans/issue-78/plan.md` — `e2b6d5b`, `6efd0be`

### Verification
- **Mutation checks** (out-of-tree copies under `.../scratchpad/mut`, never the
  worktree; run against the committed tests): 12 mutations, all killed.
  (1) AML finiteness back to the value alone — killed by 4 tests including the
  node-level thread-survival one; (2) `format_valeport` takes the raw path before
  validating; (3) same for `format_template` — each killed by the new populated-
  `raw_mm_s` test; (4) the trim rebind split into two writes, bytes first — killed
  by the atomicity test (and it *survived* the weaker first version of that
  assertion, which is why `db96536` strengthened it); (5) the node reading the two
  counters separately — killed by the once-per-tick test's raising stand-in;
  (6) the `sound_speed_bridge` guard removed entirely; (7) its live-context
  re-raise dropped (blanket swallow); (8) `except Exception` in place of the RCL
  class; (9) and (10) the same removal and blanket swallow in `zda_serial_bridge`;
  (11) `kongsberg_em_bridge` reverted to its blanket `except Exception` — killed by
  *two* tests, which is the point: it was both noisy on shutdown and silent on a
  live fault; (12) its RCL branch swallowing without consulting the context.
- The guard's semantics were checked against Jazzy rather than assumed: `RCLError`
  is not exported from `rclpy.exceptions` (the
  `rclpy.impl.implementation_singleton` route the code uses is the right one) and
  both it and `InvalidHandle` are `RuntimeError` subclasses, so the two-branch
  `except` ordering in `kongsberg_em_bridge` (RCL class first, `Exception` second)
  is what makes the narrowing effective.
- No parameter, topic, service, message or launch change in any of these commits;
  the only operator-visible text changes are the three floor strings, which are
  corrections. `sound_speed_bridge` still has no README (deferred to
  rolker/marine_tools#88) and this repo has no `.agents/README.md`, so there is no
  parameter/topic table to update.

### Notes
- Every build and test run was made from the worktree's `sensors_ws`, never with
  `colcon` inside the project repo, so no `log/` tree was generated in it.
- Nothing pushed; the PR was not touched; no issues filed (per host instruction).

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 13:19 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: changes-requested

**Branch**: feature/issue-78 at `691b717`
**Mode**: pre-push
**Depth**: Deep (reason: concurrency + shutdown paths across 4 packages), **bounded re-check** of the five fix commits `90822a8`, `e2b6d5b`, `36989be`, `6efd0be`, `db96536` (`a999d27..db96536`: 13 files, +732/-103)
**Must-fix**: 1 | **Suggestions**: 3 (+4 carried open from round 4)
**Round**: 5 | **Ship**: recommended — the single must-fix is one paragraph of plan prose; every code claim in the `## Implementation` entry verified by execution, and no code defect was found by any specialist this round.

Specialists: Static Analysis (each package's own `ament_flake8` + `ament_pep257`, inside the passing counts below, all clean); Governance; Plan Drift; one fresh-context Claude Adversarial pass (`model: sonnet`, combined logic + systemic lens, fan-out capped per host instruction). Copilot and local-model passes off (not opted in).

**Tests** — built and tested from the worktree's `sensors_ws` (never `colcon` inside the project repo). `./sensors_ws/build.sh` clean (`Summary: 4 packages finished`), then `./sensors_ws/test.sh sound_speed_bridge zda_serial_bridge kongsberg_em_bridge garmin_sidescan`; per-package lines from `colcon test-result --test-result-base sensors_ws/build/<pkg>`:

- layer-wide: `Summary: 329 tests, 0 errors, 0 failures, 0 skipped`
- sound_speed_bridge: `Summary: 137 tests, 0 errors, 0 failures, 0 skipped`
- zda_serial_bridge: `Summary: 47 tests, 0 errors, 0 failures, 0 skipped`
- kongsberg_em_bridge: `Summary: 59 tests, 0 errors, 0 failures, 0 skipped`
- garmin_sidescan: `Summary: 86 tests, 0 errors, 0 failures, 0 skipped`

Every count matches the `## Implementation` entry's claim exactly.

Verified by execution this round (out-of-tree copies under the session scratchpad; the worktree was never mutated):

- **(a) AML `1e306` and the formatter ordering.** 5 mutations, all killed. Reverting `AMLParser._parse` to value-only finiteness fails 4 tests including the node-level `test_serial_thread_survives_a_non_finite_sentence`. Restoring the raw-integer path *ahead* of validation in `format_valeport`, and separately in `format_template`, each fails `test_both_formatters_skip_a_huge_value_even_with_raw_mm_s_present` — so the ordering, not just the guard, is pinned. Changing either formatter's guard from the mm/s product to the m/s value fails 2 tests each. `format_template` now interpolates the already-validated `product` into `{value_mm_s}` rather than recomputing it.
- **(b) The three timer guards.** Each wraps the publish **call** (no `if rclpy.ok()` check-then-act anywhere in the three hunks) and consults `rclpy.ok(context=self.context)` only in the handler. 6 guard mutations, all killed: dropping the live-context re-raise in `sound_speed_bridge` or `zda_serial_bridge` fails `test_a_publish_failure_on_a_live_context_is_still_raised`; removing the `sound_speed_bridge` guard entirely fails the quiet-on-shutdown test (and flake8); reverting `kongsberg_em_bridge` to its blanket `except Exception` fails **two** tests (quiet *and* live-raise), which is what makes the narrowing load-bearing; swallowing without consulting the context fails the live-raise test; and replacing kongsberg's `except Exception` warning branch with a re-raise fails `test_a_non_rcl_error_is_still_warned_and_not_propagated` — the throttled warning for non-RCL errors is preserved and pinned. Exception-clause ordering is correct (`RCLError`/`InvalidHandle` before `Exception`; both are `Exception` subclasses, so order decides).
- **Real-SIGINT runs of all four console entry points** (I/O mocked, SIGINT delivered ~2 s in, subprocess): `sound_speed_bridge`, `zda_serial_bridge`, `kongsberg_em_bridge`, `garmin_sidescan` each **exit 0 with zero traceback lines**. (Caveat recorded, not a finding: with I/O mocked kongsberg decodes no ping, so `_sonar_info_heartbeat` returns before its publish — that path is covered by the real-dead-`Context` tests, not by this run.)
- **(c) The trim-stats tuple.** One rebind per trim event: splitting `_trim_residue`'s assignment into two whole-tuple writes fails `test_a_trim_rebinds_the_counter_pair_in_a_single_assignment` in **either** order, so `db96536`'s strengthened "every rebind moves the dropped-byte half forward" assertion does kill the mutant the weaker version survived. The node reading the two view properties instead of the `trim_stats` snapshot fails `test_trim_counters_are_snapshotted_once_per_tick`. The read-only views agree with the snapshot and reject assignment; a repo-wide grep finds no remaining write to either name. The read-modify-write inside `_trim_residue`/`_resync` is safe because `feed()` has exactly one writer (`node.py:449`, the serial loop) and the timer only reads.
- **(d) Floor wording.** All three strings corrected (node startup FATAL text, parser `ValueError`, `DEFAULT_MAX_BUFFER_BYTES` docstring). The two surviving uses of "longest legitimate sentence" (`node.py:104` parameter description, `parsers.py:137`) are the *correct* sizing guidance, not the false claim.
- **(f) SW4 attribution is honest.** The plan names the operator's standing publish-gate quote (given for [SW1]/[SW2]), the operator's post-PR confirmation of [SW3] ("garmin fix is ok"), and Copilot's independent round-3 raise as cross-confirmation, then says plainly that *the host* applied the standing decision to [SW4]. No fresh explicit approval is claimed. The round-4 must-fix on the [SW3] sentence is resolved by the same passage.

Governance: no parameter, topic, service, message or launch change. `sound_speed_bridge` has no README (deferred to rolker/marine_tools#88); `kongsberg_em_bridge`'s README documents the heartbeat's purpose and `sonar_info_period`, neither of which changed, and documents no error handling. No `.agents/README.md` in this repo. All five commits carry the agent identity; nothing pushed; no issues filed. One deliberate behaviour change (kongsberg's heartbeat now propagates an RCL failure on a live context instead of warning) is recorded in the plan's Consequences row.

### Findings
- [ ] (must-fix) Plan §6 "Counters" still states the design the branch **abandoned**: "Both are plain public attributes polled by `_publish_diagnostics`", "Two ints; no further state", and the rationale that reading both once per tick is sufficient. Commit `36989be` replaced exactly that argument — a snapshot can straddle the two *writes* of one trim, which reading once does not fix — with a single rebound tuple and read-only views. The `## Implementation` entry claims "Plan kept in sync"; this paragraph is where it is not, and it is the one place a future reader would take the superseded reasoning as the shipped design — `.agent/work-plans/issue-78/plan.md:339-361`
- [ ] (suggestion) Plan drift, additive: **Files to Change has no `sinks.py` or `test/test_sinks.py` row at all**, though both changed on this branch, and the `parsers.py` row still lists `buffer_dropped_bytes`/`buffer_trim_count` as plain attributes moved into the ABC. The mm/s-product fix — the round-3 Copilot must-fix and the largest correctness change of these five commits — appears nowhere in the plan's file table or Approach — `.agent/work-plans/issue-78/plan.md:486-505`
- [ ] (suggestion) The Implementation-notes SIGINT bullet names three packages exiting 0 under a real SIGINT; `garmin_sidescan` does too (verified this round, exit 0, no traceback). Add it or say why it is excluded — `.agent/work-plans/issue-78/plan.md:570-575`
- [ ] (suggestion) Two `test_sinks.py` cases pass `'{sound_speed_mm_s}'` as the template — a key `format_template` does not substitute. Both exercise the early-return path so they pass either way, but they are inert as template-rendering tests and would mislead anyone repurposing them; the third case correctly uses `{value_mm_s}` — `sound_speed_bridge/test/test_sinks.py:70,78`
- [ ] (carried, round 4, still open — suggestion) `garmin_sidescan.destroy_node()` does not join `_rx_thread` or the two `_aux_loop` threads, so a non-signal teardown can have a live thread publish into a torn-down publisher and re-raise. A fifth scope widening, so it is the operator's one-line yes/no — and it was **not** recorded in the plan's Consequences/Out-of-scope as round 4 asked, so pushing now loses it — `garmin_sidescan/garmin_sidescan/node.py:1085`
- [ ] (carried, round 4, still open — suggestion) `quiet_on_shutdown`'s docstring still says "a failure on a live context is re-raised unchanged, so a genuine fault is still loud" without the caveat that a real `RCLError` coinciding with a shutdown is swallowed, since the decision is made from `rclpy.ok()` after the fact rather than from what raised — `garmin_sidescan/garmin_sidescan/node.py:79`
- [ ] (carried, round 4, still open — suggestion) `kongsberg_em_bridge` and `garmin_sidescan` still have no committed real-SIGINT subprocess test (only in-process `ExternalShutdownException` and the new dead-`Context` guard tests). Both were verified by ad-hoc execution this round and last; ~10 lines each would pin the [SW2] exit-code contract against regression — `kongsberg_em_bridge/test/test_main_shutdown.py`, `garmin_sidescan/test/test_main_shutdown.py`

## Implementation
**Status**: complete
**When**: 2026-09-15 13:37 -04:00
**By**: Claude Code Agent (Claude Opus)

**PR**: #91 at `89d64e3` (branch `feature/issue-78`; not pushed by this pass)
**Addressed**: the first residual of the `## Local Review (Pre-Push)` round 5 entry
of 2026-09-15 13:19 — `garmin_sidescan.destroy_node()` not joining its daemon
threads — on the operator's explicit instruction (2026-09-15): *"fix the garmin's
destroy_node issue"*. Recorded in the plan as **[SW5]**, the fifth scope widening.
**Commits**: `382ddb1`, `ddf24fd`, `298b5d1`, `89d64e3`
**Tests** — built and tested from the worktree's `sensors_ws` (never `colcon`
inside the project repo): `./sensors_ws/build.sh garmin_sidescan` then
`./sensors_ws/test.sh garmin_sidescan`.

- garmin_sidescan: `Summary: 92 tests, 0 errors, 0 failures, 0 skipped` (was 86)

ament flake8 + pep257 are inside that count and clean. The other three packages
are untouched by this pass.

### Actions
- [x] **[SW5]** `destroy_node()` now stops, **joins** and only then tears down.
  It used to set `self._running = False`, send transmit OFF and call
  `super().destroy_node()` without waiting for any of the node's **four** daemon
  threads — `gcv_rx`, the two `_aux_loop` listeners `gcv_status` / `gcv_config`,
  and `gcv_startup` — two of which were anonymous and so could not have been
  joined at all. Every one of them publishes (imagery, nadir range and water
  temperature from `_rx_loop`; the raw status/config captures from the aux loops;
  transmit state from the startup thread), so a thread outliving the publishers
  kept working against a torn-down node — and [SW3]'s `quiet_on_shutdown` made
  that *quiet*, which is right for a signal teardown and wrong as a way to leave
  a thread running — `garmin_sidescan/garmin_sidescan/node.py` — `382ddb1`
- [x] One stop signal. `self._running` (a bool polled only at the top of each
  loop) becomes `self._stop_event`, a `threading.Event` — the same name and shape
  as `sound_speed_bridge`'s serial thread, and the thing a back-off can *wait on*
  rather than merely poll. All four threads are kept as attributes
  (`_rx_thread`, `_aux_threads`, `_startup_thread`). Nothing outside `node.py`
  referenced `_running` — `382ddb1`
- [x] **Ordering, decided and documented: joins first, transmit OFF after them,
  `super()` last.** The OFF is sent after the joins so it is the last command on
  the wire: the startup thread issues commands of its own, so a
  `transmit_on_startup` ON behind an earlier OFF would leave the sonar pinging
  unattended — the one failure this shutdown path exists to prevent. Nothing is
  lost by waiting first: an OFF is confirmed by `_send`'s own TCP `sendall` (a
  per-command socket with a 2 s timeout), **not** by anything the receive loops
  decode — they carry imagery and the device status flag, and `destroy_node`
  consults neither. The existing three-attempt OFF retry and its ERROR are
  unchanged — `382ddb1`
- [x] **Bounded joins.** `SHUTDOWN_JOIN_TIMEOUT_S = 3.0`, a budget for the
  **whole set** — each join gets what is left of it — so several wedged sockets
  cannot multiply it. 3.0 s is the longest blocking call any worker can be inside
  (`_send`'s 2.0 s TCP socket timeout, on the startup thread) plus a second of
  scheduling slack; the two receive loops block at most on `_open_mcast`'s 1.0 s
  `settimeout`. A thread that misses the budget is named in a WARN and, being a
  daemon, dies with the process: a wedged read delays shutdown by a bounded
  interval and never hangs it — `382ddb1`
- [x] **Unblocking: shorten the blocking interval, do not close the socket from
  another thread.** The receive loops' blocking read is already capped at the
  1.0 s socket timeout, so it needs nothing. What actually stalled them was the
  *back-off* — a plain `time.sleep(2.0)` on the multicast-rejoin path in both
  loops, and `time.sleep(0.3)` between the startup OFF repeats. Those become
  `self._stop_event.wait(...)`, which returns the moment the stop is signalled.
  Closing the sockets from `destroy_node()` was considered and rejected: they are
  locals owned by the loops, which close and reopen them on every `OSError`, so a
  cross-thread close would race the reopen and hand a live loop a closed fd — for
  no gain, since the 1.0 s timeout already bounds the read — `382ddb1`
- [x] The startup thread returns early once the stop is signalled, before its
  range command and before a `transmit_on_startup` ON, so a shutdown landing
  mid-startup cannot put a command on the wire behind the shutdown OFF —
  `382ddb1`
- [x] Six tests in a new `garmin_sidescan/test/test_shutdown_joins.py`, five of
  them against a **real** node with its multicast sockets and TCP command sends
  faked out: every thread that was alive is joined and gone once `destroy_node()`
  returns; a read that never returns costs the budget and not the process (and
  the stuck thread is named in the WARN); a thread parked in its rejoin back-off
  — an unreachable GCV, the ordinary case when the boat is being packed up — is
  joined at once; the transmit OFF is sent only once every thread is dead; the
  pre-existing OFF retry and ERROR still hold; and the startup thread issues no
  further command once stopping — `ddf24fd`
- [x] Plan kept in sync: **[SW5]** recorded as a fifth scope widening (revision 7
  note, its own Scope-widening section quoting the operator's instruction
  verbatim, a Files-to-Change row for `node.py` and one for the new test file, a
  Consequences row naming both deliberate behaviour changes), and the residual
  removed from the round-5 list now that it is done — `298b5d1`

### Verification
- **Mutation checks** (out-of-tree copy under the session scratchpad; the
  worktree was never mutated; run against the committed tests): 8 mutations, all
  killed. (1) `destroy_node` joins nothing — killed by 3 tests; (2) only
  `_rx_thread` joined — killed by the back-off test, on `gcv_startup` surviving;
  (3) unbounded `thread.join()` — killed by the wedged test (it ran 31 s instead
  of 4 s); (4) the transmit OFF moved back ahead of the joins — killed by the
  ordering test; (5) the startup thread's early return removed; (6) its repeat
  pause reverted to `time.sleep(0.3)` — each killed by the startup test; (7) both
  rejoin back-offs reverted to `time.sleep(2.0)` — killed by the back-off test;
  (8) the missed-budget WARN dropped — killed by the wedged test.
- **Real SIGINT of the `garmin_sidescan` console entry point** (I/O mocked,
  SIGINT delivered 2 s in, subprocess): **exit 0, zero traceback lines**, and no
  join WARN on the way out — every thread was joined inside the budget. This is
  the [SW2]/[SW3] contract re-checked on top of the new teardown, and it is the
  package the round-5 residual list notes has no *committed* SIGINT test (still
  true; that residual is untouched).
- No parameter, topic, service, message or launch change. The only new
  operator-visible output is the WARN naming a thread that missed the join
  budget, so no parameter or topic table changes. The package README's **Transmit
  safety** section did need updating and got it (`89d64e3`): it said only that
  transmit-off is sent on shutdown, and the two facts an operator now depends on
  — that the OFF is sent *after* the joins, so a `transmit_on_startup` ON cannot
  land behind it, and that the joins are bounded so a wedged read delays the OFF
  without blocking it — are exactly about the behaviour that section exists to
  describe. This repo has no `.agents/README.md`.

### Notes
- Every build and test run was made from the worktree's `sensors_ws`, so no
  `log/` tree was generated in the project repo.
- Nothing pushed; the PR was not touched; no issues filed (per host instruction).
- The other round-5 findings (the plan §6 counters paragraph, the missing
  `sinks.py` rows, the SIGINT-notes bullet, the two `test_sinks.py` template
  keys, the `quiet_on_shutdown` docstring caveat, the missing committed
  real-SIGINT tests) were **not** in this pass's instruction and are untouched.

## Integrated Review
**Status**: complete
**When**: 2026-09-15 13:40 -04:00
**By**: Claude Code Agent (Claude Fable 5.1)

**PR**: #91 at `6d5d84b` (reviewed head; local head now 169d108 after [SW5])
**Sources**: 3 (Copilot R4 @ `6d5d84b` — 1 inline + 11 suppressed; Local Review (Pre-Push) round 5 residuals; CI rollup)
**Cross-source confirmations**: 2
**CI**: all-pass

### Findings
- [x] (cross-confirmed: Copilot + Local Review round 1 deferred suggestion) the serial thread's `_handle_reading()` publishes `sound_speed`/`raw` with no call-level shutdown guard; a SIGINT between the `_stop_event` check and a publish lets `RCLError` escape `_serial_loop` as a thread traceback — apply the [SW4] guard to those publishes with a forced-ordering regression test — `sound_speed_bridge/node.py:765` (and `_handle_reading`)
- [x] (cross-confirmed: Copilot ×2 + Local Review round 5 residual) `garmin_sidescan` and `kongsberg_em_bridge` have no committed real-SIGINT subprocess test; add the harness the other two packages have — `garmin_sidescan/node.py:1122`, `kongsberg_em_bridge/node.py:749`
- [x] (must-fix, Copilot inline + suppressed ×2) "a line longer than the cap can never frame" is too absolute: the cap applies after framing, so a long line frames when it and its terminator arrive in one `feed()`; only residue exceeding the cap before a terminator is discarded — reword in `parsers.py:138`, the node parameter description (`node.py:106`), and `plan.md:307`
- [x] (low, Copilot) stale comment: parser no longer owns two plain ints; it is one `_trim_stats` tuple with read-only views — `node.py:207`
- [x] (low, Copilot ×3) plan records: self-check "two source files, three test files" is stale (`plan.md:520`); verification record omits `garmin_sidescan` (`plan.md:586`); #88 out-of-scope line should list the two counters too (`plan.md:486`)
- [x] (low, Copilot) PR description floor wording — updated host-side
- [x] (carried, Local Review round 5) `quiet_on_shutdown` docstring overstates "a genuine fault stays loud" — add the coinciding-shutdown caveat

### False positives
- (Copilot, `node.py:103`) "add the parameter/counter/topic README with this change" — deferred by explicit operator decision at the #77 publish gate (README filed as rolker/marine_tools#88, scope widened by comment to carry `parser_max_buffer_bytes`, `buffer_dropped_bytes`, `buffer_trim_count`); not a defect in this PR.

## Implementation
**Status**: complete
**When**: 2026-09-15 13:52 -04:00
**By**: Claude Code Agent (Claude Opus 5 (1M context))

**PR**: #91 at `565b738` (branch `feature/issue-78`; not pushed)
**Addressed**: `## Integrated Review` (Copilot round 4), When 2026-09-15 13:40 -04:00, reviewed head `6d5d84b`
**Commits**: 9b4316e, 2279c45, e5b618f, e841a54, 60dd149, 565b738

### Actions
- [x] (cross-confirmed) [SW4] call-level guard on the serial thread's publishes — the body of `_handle_reading` moves into `_publish_reading()` so one guard covers `sound_speed`, `raw`, `temperature`/`pressure` and the UDP error path's logging; `RCLError`/`InvalidHandle` caught, `rclpy.ok(context=...)` consulted only after the failure, non-RCL errors not swallowed. `_publish_serial_tap` needs no guard (its own broad counted/logged except). Four forced-ordering tests against a real shut-down `Context`, including a synchronous `_serial_loop` run that must consume every chunk — `sound_speed_bridge/sound_speed_bridge/node.py:609`, `sound_speed_bridge/test/test_shutdown_guard.py` (9b4316e)
- [x] (cross-confirmed) real-SIGINT subprocess tests for `garmin_sidescan` and `kongsberg_em_bridge`, matching the harness the other two packages carry (I/O mocked, real SIGINT, exit 0 and no traceback, `timeout=120`, no skip conditions) — `garmin_sidescan/test/test_main_shutdown.py`, `kongsberg_em_bridge/test/test_main_shutdown.py` (2279c45)
- [x] (must-fix) "a line longer than the cap can never frame" reworded in all three places: a long line still frames when it and its terminator arrive within one `feed()`; only residue reaching the cap before a terminator is trimmed and resynced away, so an undersized cap loses the sentences that straddle a read boundary. Sizing guidance kept — `sound_speed_bridge/sound_speed_bridge/parsers.py:130`, `node.py:106`, `plan.md:418` (e5b618f)
- [x] (low) stale counter comment now describes the single `_trim_stats` tuple and its read-only views — `sound_speed_bridge/sound_speed_bridge/node.py:201` (e841a54)
- [x] (carried) `quiet_on_shutdown` docstring states the trade: a real RCL fault coinciding with a shutdown is swallowed, because after the fact the two are indistinguishable; loud while running, quiet while shutting down — `garmin_sidescan/garmin_sidescan/node.py:88` (60dd149)
- [x] (low) plan records: self-check now says four packages rather than "two source files, three test files"; the verification record covers all four entry points including `garmin_sidescan` and notes the execution is now committed as tests; the #88 out-of-scope line names `parser_max_buffer_bytes`, `buffer_dropped_bytes` and `buffer_trim_count` — `plan.md` (565b738, 2279c45)

### Plan sync

The [SW4] extension is recorded in `plan.md` as an extension of [SW4] (same defect class, same package, same operator standing decision, raised by Copilot on the PR) — not a new SW number: new `#### [SW4] extension` subsection, plus the Consequences and Files-to-Change rows. The round-5 residuals list is now empty: both remaining items were addressed above.

### Deferred

- none. The README finding stands as the review recorded it — a false positive by explicit operator decision at the #77 publish gate (README is rolker/marine_tools#88); no README was written and the checkbox was left as-is.

### Verification

Built and tested from the worktree's `sensors_ws` (`./sensors_ws/build.sh` / `./sensors_ws/test.sh`, all four packages). `colcon test-result --test-result-base sensors_ws/build/<pkg>`:

- `sound_speed_bridge`: `Summary: 141 tests, 0 errors, 0 failures, 0 skipped`
- `zda_serial_bridge`: `Summary: 47 tests, 0 errors, 0 failures, 0 skipped`
- `kongsberg_em_bridge`: `Summary: 60 tests, 0 errors, 0 failures, 0 skipped`
- `garmin_sidescan`: `Summary: 93 tests, 0 errors, 0 failures, 0 skipped`

flake8 and pep257 run inside each of the four suites and are clean.

**Mutation checks** (out-of-tree copies under the scratchpad; the worktree was never mutated):

- removing the `_handle_reading` guard fails `test_a_reading_publish_is_quiet_once_the_context_is_shut_down` and `test_the_serial_loop_survives_a_shutdown_race_on_a_publish` (2 failed, 5 passed)
- restoring the pre-fix `main()` (`except KeyboardInterrupt` + plain `rclpy.shutdown()`) fails the new `test_sigint_exits_zero_without_a_traceback` in both `garmin_sidescan` and `kongsberg_em_bridge`

### Next step

Lifecycle: **Implementation** → **review-code** (fresh-context re-review of these fixes).

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 13:59 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: changes-requested

**Branch**: feature/issue-78 at `7ed6c1f`
**Mode**: pre-push (bounded re-check of the 13 commits since the pushed head `6d5d84b`)
**Depth**: Standard-equivalent, scoped (concurrency/lifecycle lens on [SW5]; one sonnet
adversarial pass, `model: sonnet` explicit; no --copilot, no --local)
**Must-fix**: 1 | **Suggestions**: 3
**Round**: 6 | **Ship**: recommended — the single must-fix is a comment/docstring/README
wording correction (plus a one-line join-order strengthener); nothing behavioural is
unsafe, and the count is down from round 5 rather than rising.

### Scope re-checked
[SW5] garmin `destroy_node()` thread joins (`382ddb1`, `ddf24fd`, `298b5d1`, `89d64e3`);
the [SW4] extension and round-4 fixes (`9b4316e`, `2279c45`, `e5b618f`, `e841a54`,
`60dd149`, `565b738`).

### Verification performed this round
- All four packages built and tested from the worktree's `sensors_ws`
  (`./sensors_ws/build.sh` then `./sensors_ws/test.sh`, never `colcon` inside the project
  repo). `colcon test-result --test-result-base sensors_ws/build/<pkg>`:
  `sound_speed_bridge` `Summary: 141 tests, 0 errors, 0 failures, 0 skipped`;
  `zda_serial_bridge` `Summary: 47 tests, 0 errors, 0 failures, 0 skipped`;
  `kongsberg_em_bridge` `Summary: 60 tests, 0 errors, 0 failures, 0 skipped`;
  `garmin_sidescan` `Summary: 93 tests, 0 errors, 0 failures, 0 skipped`. ament flake8 and
  pep257 run inside each suite and are clean, so static analysis is covered.
- Independent mutation checks, out-of-tree copies under the session scratchpad (the
  worktree was never mutated, no `git checkout --`/`restore` on tracked files):
  restoring the pre-fix `main()` (`except KeyboardInterrupt` + `rclpy.shutdown()`) fails
  the new `test_sigint_exits_zero_without_a_traceback` in **both** `garmin_sidescan` and
  `kongsberg_em_bridge`; removing `self._join_workers()` from `destroy_node()` fails 4 of
  the 6 tests in `test_shutdown_joins.py`. The new tests are load-bearing, not decorative.
- Flakiness probe: `test_shutdown_joins.py` + `test_main_shutdown.py` run 3x under 8
  concurrent CPU-burner processes — 12 passed each time, ~9.5 s per run. Neither new test
  binds a real port or touches the network.
- Startup-thread early return (`node.py:543`) is unreachable on a healthy start:
  `_stop_event` is set in exactly one place, `destroy_node()`. Nothing is skipped when not
  shutting down.
- `destroy_node()` is idempotent once construction completed: `_stop_event.set()` is
  idempotent, `_join_workers()` skips dead threads, `super().destroy_node()` is guarded by
  rclpy. Both receive loops close their socket on the way out (`node.py:753`, `node.py:800`).
- Both new SIGINT harnesses fake `socket.socket` wholesale, so the driver's real loops run
  with no network and nothing binds :50050/:51000/:20002.

### Findings
- [ ] (must-fix) The 3.0 s join budget is derived from a worst case that is short by ~2x,
  and three places state a shutdown bound the code does not guarantee. `sock.settimeout(2.0)`
  is **per blocking operation**, so one `_send()` can spend up to 2 s in `connect()` and a
  further 2 s in `sendall()` — ~4 s, not the "2.0 s TCP socket timeout" the constant's
  comment calls "the longest blocking call any worker can be inside". Because
  `_join_workers()` joins `_startup_thread` **last**, a wedged receive thread can consume
  the whole budget first; the startup thread is then abandoned with `join(0.0)` while still
  holding `_send_lock`, and `destroy_node()`'s own `_send(TRANSMIT_OFF)` blocks on that
  plain, timeout-less `with self._send_lock:` until it clears. The OFF still goes out and
  the process still exits (daemons + per-op socket timeouts bound it), and safety is not
  breached — the startup thread's early return means it can never put an ON behind the OFF
  — but an operator told "3 s for the whole set" can wait meaningfully longer. Fix the
  claim in all three places, and consider the one-line strengthener of joining
  `_startup_thread` **first**: it is the only worker that takes a lock `destroy_node()`
  needs, so giving it the full budget makes the stated guarantee nearly true —
  `garmin_sidescan/garmin_sidescan/node.py:62-69`, `node.py:1168-1171`,
  `garmin_sidescan/README.md:110-114`
- [ ] (suggestion) No test covers that lock-contention path: `test_a_wedged_thread_does_not_hang_destroy_node`
  wedges only `_open_mcast`/`recvfrom`, and its startup thread uses the fixture's instant
  `_send`, so it never holds `_send_lock` past the deadline — `garmin_sidescan/test/test_shutdown_joins.py:104`
- [ ] (suggestion) `test_a_wedged_thread_does_not_hang_destroy_node` brackets elapsed time
  to `[budget-0.5, budget+2.0]`. It survived 3 runs under 8x CPU load here (~3.0 s), so
  this is precautionary, not observed: widening the upper bound costs nothing and removes
  the only wall-clock assertion in the suite that a loaded runner could trip —
  `garmin_sidescan/test/test_shutdown_joins.py:123`
- [ ] (suggestion, latent / out of this branch's scope) `_join_workers()` dereferences
  `_rx_thread` / `_aux_threads` / `_startup_thread` unguarded, and those are created ~90
  raise-capable lines after `_stop_event`. Unreachable today because `main()` constructs the
  node **outside** its `try`, so a constructor failure never reaches `destroy_node()` — but
  that is also why a garmin construction failure is a raw traceback rather than the one
  FATAL line + exit 1 that `sound_speed_bridge` got as [PR-R1-S7]. Fixing it would be a
  sixth scope widening; recording it here rather than doing it —
  `garmin_sidescan/garmin_sidescan/node.py:1138`, `node.py:1201`

### Cleared this round
- **[SW4] extension** — the guard wraps `_publish_reading()` as a single call; the UDP
  path's `sendto` still raises `OSError` caught locally, and the UDP error path's
  `get_logger().warning` is now covered by the same guard (an improvement, not a
  regression). The live-context re-raise is preserved (bare `raise` after
  `rclpy.ok(context=...)`), non-RCL exceptions are deliberately not caught, and
  `_serial_loop`'s own `except (SerialException, OSError)` is unchanged (comment-only edit
  at `node.py:462`). The `_ReadingProxy` tests re-enter the unbound methods, so the guard
  really consults the proxy's dead context.
- **Wording (d)** — the over-absolute "a line longer than the cap can never frame" is gone
  from all three sites (`parsers.py:137`, `node.py:105`, `plan.md:420`); a repo-wide grep
  finds no surviving instance, and the remaining "never frames" hits are the unrelated and
  correct wrong-baud/wrong-terminator claims.
- **Plan drift (f)** — plan is in sync: [SW5] has its own section, Scope-widening entry
  quoting the operator's instruction, Consequences row and two Files-to-Change rows; the
  [SW4] extension is recorded as an extension rather than a new number; `sinks.py` /
  `test_sinks.py` rows are present; the §6 counters paragraph now describes the `trim_stats`
  tuple; the verification bullet now names all four packages and says the SIGINT execution
  is committed as tests. The round-5 residual list is genuinely empty.
- **Attribution** — [SW5] rests on the operator's direct instruction ("fix the garmin's
  destroy_node issue"), quoted verbatim in the plan; no standing decision is stretched to
  cover it. The `sound_speed_bridge` README finding stays recorded as a false positive by
  explicit operator decision at the #77 publish gate (README is rolker/marine_tools#88).
- **Governance** — no parameter, topic, service, message or launch change. The one
  operator-visible text change (`parser_max_buffer_bytes`'s description) is already carried
  by #88. `garmin_sidescan/README.md`'s Transmit safety section was updated with the change
  (modulo the must-fix wording above). All 13 commits carry the agent identity; nothing
  pushed.

## Implementation
**Status**: complete
**When**: 2026-09-15 14:02 -04:00
**By**: Claude Code Agent (Claude Fable 5.1)

**PR**: #91 (host-inline fix for the round-6 must-fix)

### Findings
- [x] (must-fix, round 6) join budget worst case was 2× short and the startup thread was joined last while holding `_send_lock` — startup thread now joined first, budget 5.0 s (4 s connect+sendall worst case + slack), claims corrected in the constant comment, `destroy_node` docstring and README
- [ ] (suggestion, round 6) lock-contention path untested — deferred
- [ ] (suggestion, round 6) wedged-thread timing bracket is precautionary — deferred
- [ ] (latent, round 6) garmin `main()` constructs outside its `try` — recorded in plan as a sixth-widening candidate for the operator

garmin_sidescan: `Summary: 93 tests, 0 errors, 0 failures, 0 skipped`; layer: 341/341.

## Integrated Review
**Status**: complete
**When**: 2026-09-15 14:15 -04:00
**By**: Claude Code Agent (Claude Fable 5.1)

**PR**: #91 at `3e68e1d`
**Sources**: 2 (Copilot R5 @ `3e68e1d` — 5 inline + 4 suppressed; CI rollup)
**Cross-source confirmations**: 0
**CI**: all-pass

### Findings (all fixed host-inline; operator wrap-up at 15:00, no separate local review round — changes are small and each carries a test)
- [x] (must-fix, Copilot) garmin startup thread: stop checked only before the range command; a stop landing during that send could let a startup ON follow the final OFF — ON now gated on the stop event; test
- [x] (should-fix, Copilot ×2) `InvalidHandle` after the node's own stop is teardown even with a live context (worker outliving the bounded join) — garmin decorator and sound_speed_bridge guard treat the stop event as teardown; tests on a live context
- [x] (should-fix, Copilot) `main()` caught only `ValueError`; an override of the wrong ROS type raises `InvalidParameterTypeException` before validation — caught, one FATAL, exit 1; test with `:=4096.0`
- [x] (low, Copilot) README claimed an unconditional join — now "bounded join attempted; the stop event prevents a later command"

### False positives
- (Copilot ×4 inline, all four nodes) "`InvalidHandle` is provided by `rclpy.handle`, not `rclpy.exceptions`; the import fails at load" — verified in the Jazzy environment: `from rclpy.exceptions import InvalidHandle` succeeds and `rclpy.handle` does not exist (ModuleNotFoundError); the committed real-SIGINT subprocess tests load and run every entry point.
