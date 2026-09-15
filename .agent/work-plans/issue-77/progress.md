---
issue: 77
---

# Issue #77 — sound_speed_bridge: byte-stream tap from _serial_loop for wrong-baud diagnosis (raw topic only carries framed sentences)

## Issue Review
**Status**: complete
**When**: 2026-09-15 08:39 -04:00
**By**: Claude Code Agent (Claude Sonnet)

**Issue**: #77
**Comment**: (best-effort post follows this entry; not recorded inline)
**Scope verdict**: well-scoped

### Scope Assessment

**Well-scoped?** Yes. Single package (`sound_speed_bridge`), single change point
(`SoundSpeedBridgeNode._serial_loop` in `sound_speed_bridge/sound_speed_bridge/node.py`,
where `ser.read(256)` currently happens before `self._parser.feed(data, now_ns)`).
The issue names three open design points itself ("Design points to settle") —
that's normal plan-task input, not a sign the issue needs more detail from the
reporter. One PR is the right size.

**Right repo?** Yes. This is sensor-driver domain code (workspace vs. project
separation) — belongs in `marine_tools`, not the workspace repo.

**Dependencies**:
- #78 ("parsers: unbounded accumulation buffer — cap + trim WARN") explicitly
  touches the *same* pre-framing path and both issues ask to coordinate with
  each other. Verified from source: #78's buffer is `SoundSpeedParser`'s internal
  accumulation buffer (inside `feed()`), while #77's tap point
  (`ser.read(256)` in `_serial_loop`) is *upstream* of that buffer and bounded
  per-call to 256 bytes regardless of how #78 resolves. So #77 does not
  functionally block on #78 landing first — but a tap chunked by raw `read()`
  calls will not by itself reproduce #78's "one multi-MB sentence" bag-volume
  risk (good), and the two should still cross-link their PRs since both edit
  the same function region and either could land first.
- #75 / PR #76 (closed/merged) — established the existing per-sentence `raw`
  topic and its documented limitations; #77's own issue body cites the
  concrete verification (22 wire bytes -> 18 published) done during that
  PR's round-2 review.
- rolker/unh_echoboats_project11#396 — cross-repo tracking issue for the AML
  field-diagnosis chain; informational only, no action needed here.
- No open PR exists yet for either #77 or #78.

### Principle Alignment

| Principle | Status | Notes |
|---|---|---|
| Human control and transparency | OK | New topic is observable/bag-recordable; no hidden behavior change to existing `sound_speed`/`raw` topics implied by the ask |
| Capture decisions, not just implementations | Watch | The issue leaves 3 real design decisions open (replace-vs-complement the `raw` topic; publish cadence/chunking; ordering with #78). plan-task should record the chosen design and rationale in the plan (and in code comments, matching the existing style at `node.py`'s `raw` publisher, which already documents its own scope/limits) — not just implement a default |
| Only what's needed | Watch | Publishing every `ser.read(256)` chunk as its own message is the simplest cadence but could materially raise message rate / bag volume relative to the existing per-sentence `raw` topic, especially since `read()` returns as soon as 1 byte is available after the 1s timeout elapses with partial data. Cadence/chunking choice should be made deliberately, not defaulted without comment |
| A change includes its consequences | Action needed | No package README exists for `sound_speed_bridge` (pre-existing gap, not introduced by this issue — the prior `raw` topic PR #76 also shipped without one). Adding a second raw-adjacent topic increases the case for at least a doc comment set (already the existing pattern: `node.py`'s `raw` publisher carries an in-code doc-comment explaining its scope/limits) or a short package README. Not blocking, but plan-task should decide which |
| Test what breaks | Action needed | This is exactly the "hard to find in the field" failure mode the principle calls out (wrong-baud UART misconfiguration, bus-voltage-sag bit corruption per the host-supplied field evidence). Plan must include tests that: (a) the tap captures unframeable garbage bytes that never reach a parser callback, (b) no byte loss/duplication/reordering across chunk boundaries, (c) behavior under the `_stop_event` shutdown race already handled for `_handle_reading` (see existing shutdown-guard comment in `node.py`) |
| Workspace vs. project separation | OK | Project-specific sensor driver work, correctly scoped to `marine_tools` |
| Improve incrementally | OK | Builds on #75/PR#76 rather than reworking it |

### ADR Applicability

| ADR | Triggered | Notes |
|---|---|---|
| ADR-0008 (ROS 2 conventions) | Yes | A new topic/publisher should match the existing `raw` topic's established conventions in this file: bare relative topic name (not under the node name), `RELIABLE` QoS, `UInt8MultiArray` message type — unless the design explicitly needs to diverge (e.g., a different QoS for a higher-rate tap), which should then be justified in the plan |
| ADR-0013 (progress.md vocabulary) | Yes (process) | This review itself follows the vocabulary; downstream plan-task/review-plan/implementation phases must also |
| Others (0001–0007, 0009–0012, 0014–0019) | No | Not triggered — no new ADR-worthy decision, no Python packaging change, no deployment-mode/worktree-process change beyond the standard lifecycle |

### Consequences

- If the tap changes what's captured in deployment bags, the field-diagnosis
  chain docs/issues referencing "the `raw` topic" (#75, echoboats#396) should
  be updated to point at whichever topic is now canonical for wrong-baud
  diagnosis — plan-task should decide replace-vs-complement explicitly and
  note the outcome for anyone reading #75/#396 later.
- If publish cadence is naive (one message per `read()` return), consider a
  diagnostics counter/rate similar to `_publish_diagnostics`'s existing
  pattern (`_parse_error_count`, `_serial_reconnect_count`) so an operator
  can see the tap is alive without inspecting bag content.

### Recommendations

- Settle the three design points in the plan (plan-task), with explicit
  rationale recorded — don't let cadence/chunking or replace-vs-complement
  default silently.
- Cross-link #77 and #78 in whichever PR lands first, since both touch
  `_serial_loop`/`parsers.py`'s shared pre-framing region — reduces merge
  conflict surprise even though neither functionally blocks the other.
- Consider whether a short `sound_speed_bridge` README is now due, given two
  raw-adjacent topics will exist after this lands.

### Actions
- [ ] Record replace-vs-complement decision for the existing `raw` topic, with rationale, in the plan
- [ ] Record publish cadence/chunking decision, with rationale (bag-volume/rate impact), in the plan
- [ ] Cross-link with #78 in the PR description (shared `_serial_loop`/`parsers.py` region), even though #77 does not functionally block on #78
- [ ] Add tests for unframeable-garbage capture, no byte loss/duplication across chunks, and the existing shutdown-race guard applied to the new publish path
- [ ] Decide whether a `sound_speed_bridge` README is now warranted (pre-existing gap, not required to fix here, but worth a deliberate yes/no)

## Plan Authored
**Status**: complete
**When**: 2026-09-15 08:42 -04:00
**By**: Claude Code Agent (Claude Sonnet)

**Plan**: `.agent/work-plans/issue-77/plan.md` at `5f8cc9b`
**Branch**: feature/issue-77 at `5f8cc9b`
**Phases**: single

### Open questions
- [ ] No open questions — plan is review-plan-ready.

## Plan Review
**Status**: complete
**When**: 2026-09-15 08:46 -04:00
**By**: Claude Code Agent (Claude Opus)

**Plan**: `.agent/work-plans/issue-77/plan.md` at `5f8cc9b`
**PR**: PR-less (dispatched fresh-context sub-agent; independent of the plan author)
**Verdict**: changes-requested

### Evaluation

| Dimension | Verdict | Notes |
|---|---|---|
| Scope | Good | Two files, one publisher, one counter, four tests — single PR |
| Issue alignment | Good | All three design points the issue left open are settled with rationale; all five `## Issue Review` actions are addressed |
| File targeting | Needs work | `node.py` + `test_node.py` are right, but the test harness change `_make_node` needs (finding 6) is unlisted, and two stale in-file comments are missed (finding 2) |
| Consequences | Needs work | Reconnect discontinuity unqualified (3); `/diagnostics` record-list dependency unstated (4) |
| Documentation & instruction impact | Concern | Section is present and non-silent, but its "Stale docs: None" claim is factually wrong — see finding 2 |
| Principle alignment | Needs work | "Only what's needed" volume rationale rests on a wrong derivation (5); "Improve incrementally" broken by the publish-ordering change (1) |
| ADR compliance | Good | ADR-0008 satisfied (bare relative name, RELIABLE, `UInt8MultiArray`, matching the `raw` publisher); ADR-0013 followed |
| ROS conventions | Good | One minor unrecorded decision: `UInt8MultiArray` has no header, so bag receive time is the tap's only time base (8) |

### Field-evidence check (host scenario, 17 BizzyBoat bags)

- **Silent vs. corrupt**: the corrupt case is served — glitch bytes and all-NUL chunks still return from `ser.read(256)` and are published verbatim, so the bag shows bytes arriving that never framed. The silent case is inferred from *absence* of tap messages, which is only sound if the `tap_byte_count` diagnostic is in the bag; see finding 4.
- **Volume**: acceptable. At the field probe's 25 Hz with an 11-byte AML sentence (`1500.123\r\r\n`) the wire carries ~275 B/s, so `read(256)` returns roughly once per second → ~1.1 Hz tap, ~32k messages and ~8 MB of payload over an 8 h recording. Hard bound is baud/10 = 960 B/s at the default 9600 baud → ≤27.6 MB per 8 h. The plan's own estimate (~4–8 Hz, 1–2 KB/s) is ~4x high but conservative; see finding 5.

### Findings
- [ ] (must-fix) Tap publish placed before `self._parser.feed()` violates this file's explicit exception-isolation rule (`node.py:219-221`): `_serial_loop` catches only `(SerialException, OSError)`, so an unexpected error from the diagnostic publish kills the serial thread and stops all readings — failing in exactly the degraded condition the tap exists for. Publish after the feed loop, or wrap in its own `try/except`, and record which — `plan.md` Approach step 2
- [ ] (must-fix) "Stale docs: None" is wrong: `node.py:107` ("See rolker/marine_tools#77 for a true byte-stream tap") and `test/test_node.py:9` both describe this tap as future work and must be repointed at `serial_tap` in this PR — both are in files the plan already edits — `plan.md` Documentation & Instruction Impact
- [ ] (should-fix) Byte-exact-reconstruction claim is unqualified but false across a serial reconnect (`node.py:184-190` reopens after SerialException with no in-band marker) — the bus-sag case the field evidence produces; qualify it and name `serial_reconnect_count` as the cross-check — `plan.md` Design Decision 2
- [ ] (should-fix) Distinguishing "probe silent" from "node/topic absent" rests entirely on the new `tap_byte_count` diagnostic, so `/diagnostics` must be in the deployment bag record list (echoboats#396, the same contract the `raw` test cites); state that as a consequence — `plan.md` Approach step 3 / Consequences
- [ ] (should-fix) Rate/volume rationale derives the tap rate from *baud* (link capacity) rather than the probe's sentence rate; restate with the field numbers above and the baud/10 hard bound — same conclusion, sounder record — `plan.md` Design Decision 2
- [ ] (should-fix) All four new tests must drive `_serial_loop`, but the shared `_make_node` helper deliberately kills the serial thread right after construction (`test_node.py:31-62`) and every existing test calls `_handle_reading` directly; name the harness work (second helper or direct `_serial_loop()` call with a `side_effect` that sets `_stop_event`) and preserve `_make_node`'s busy-spin rationale — `plan.md` Approach step 4
- [ ] (suggestion) Context attributes non-framing to "the CRLF regex framer", but the default/field parser is `aml`, which frames on a single `\r` (`parsers.py:76`); conclusion holds for both, but the rationale record should name the right framer — `plan.md` Context
- [ ] (suggestion) Record as a decision that `UInt8MultiArray` carries no header, so bag receive time is the tap's only time base — acceptable (`raw` is the same) but post-hoc temporal correlation is the tap's whole purpose — `plan.md` Approach step 1

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 09:04 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: changes-requested

**Branch**: feature/issue-77 at `8f1b254`
**Mode**: pre-push
**Depth**: Deep (reason: 898 changed lines, ≥200 threshold; concurrency/lifecycle change in a sensor driver)
**Must-fix**: 2 | **Suggestions**: 5
**Round**: 1 | **Ship**: continue — 2 must-fix at round 1, both mechanical and precisely located; next round should converge

**Specialists**: Static Analysis (ament_flake8 + ament_pep257, clean), Governance, Plan Drift, Claude Adversarial Lens A + Lens B. Copilot and Local cross-model reads off (not opted in). Lead reviewer additionally mutation-tested the new tests and ran the suite (50/50 green).

### Findings
- [x] (must-fix) Ordering invariant "tap publishes after the parser feed" is documented as load-bearing but no test guards it — moving the publish before `_parser.feed()` leaves all 50 tests green (verified empirically by mutation); assert relative call order between the primary publishes and the tap — `sound_speed_bridge/sound_speed_bridge/node.py:203-208`, `sound_speed_bridge/test/test_node.py`
- [x] (must-fix) Cross-repo consequence is recorded only as plan prose and will evaporate: `serial_tap` is absent from bizzyboat.yaml's `logger`/`sonar_logger` record lists, and bizzyboat.yaml still describes marine_tools#77 as future work — file the unh_echoboats_project11 follow-up and reference it in the PR body, or the tap is inert in the field — `bizzyboat_project11/config/bizzyboat.yaml:813-818,709,919` (cross-repo) (deferred: host-handled — the host files the unh_echoboats_project11 follow-up at the publish checkpoint and references it in the PR body; no change in this repo)
- [x] (suggestion) `_handle_reading`'s four publishes have no exception isolation at all while the new tap does — same thread, same shutdown path; pre-existing, but this PR draws the contrast, so file it as a follow-up — `sound_speed_bridge/sound_speed_bridge/node.py:269-292` (deferred: pre-existing behaviour, out of scope for this PR — recorded as a follow-up candidate)
- [x] (suggestion) Byte-exactness caveat covers reconnects but not transport: RELIABLE + KEEP_LAST(10) can still drop samples if a subscriber falls more than 10 behind — one sentence in the publisher comment — `sound_speed_bridge/sound_speed_bridge/node.py:113-125` (deferred: host decision — comment wording change held for a later pass)
- [x] (suggestion) `tap_byte_count` under-reports wire traffic when publishes fail (bytes counted only on success, errors counted per chunk not per byte); consider a bytes-read counter independent of publish success — `sound_speed_bridge/sound_speed_bridge/node.py:236-243`
- [x] (suggestion) File the deferred `sound_speed_bridge` README (covering `raw` + `serial_tap` + the parameter surface) as a real issue rather than leaving it as plan prose — `.agent/work-plans/issue-77/plan.md` Design Decision 4 (deferred: host decision — issue filing is the host's at the publish checkpoint; this sub-agent files no issues)
- [x] (suggestion) Knowledge-doc candidate, operator's call: the "diagnostic publish placed after the primary path, wrapped in a counted + throttled-logged broad except" idiom is reusable across this workspace's serial/UDP bridge nodes — proposal only, no auto-edit (deferred: host decision — proposal recorded, no edit)

### Adjudicated false positives (recorded so they are not re-raised)
- Lens B "shutdown TOCTOU can surface as a native use-after-free/segfault the broad `except` cannot catch": **rejected**. `rclpy.publisher.Publisher.publish` enters `self.handle` (`Destroyable.__enter__`), which raises `InvalidHandle` — verified a Python `Exception` subclass — and holds a use-count that defers destruction while a publish is in flight. The guard-plus-broad-`except` design is correct and sufficient.
- Lens B "the `except` block's `get_logger()` could itself raise and kill the thread": dropped, not substantiated — `get_logger()` returns a plain attribute and rclpy logging holds no rcl handle.
- Lens B "tap publishes more often than `raw`, so its RELIABLE-QoS blocking risk is higher": **factually inverted** — the tap is ~1 Hz (one per `read()` return), `raw` is ~25 Hz (one per sentence) on the same profile in the same thread. The tap adds roughly 1/25 of the pre-existing exposure; RELIABLE retained as consistent with `raw`, repo convention, and ADR-0008.
- Lens A "parsers.py unbounded accumulation buffer": already tracked as marine_tools#78 (OPEN, verified) — out of scope here.

### Verification performed by the lead reviewer
- Suite: 50/50 green. `ament_flake8` + `ament_pep257` clean on both changed files.
- Mutation tests: deleting the `_stop_event` guard fails `test_serial_tap_noop_after_stop`; deleting the `try/except` fails `test_serial_tap_publish_failure_is_counted_not_fatal`; suppressing the tap when the parser yields nothing fails 4 tests. Moving the tap publish before the parser feed fails **nothing** — the basis of must-fix 1.
- Field fidelity: the `_field_regex_parser` fixture matches the real BizzyBoat launch (`parser: regex`, `regex_line_terminator: crlf`, same `$AML,SVM` pattern) — confirmed against `sound_speed_launch.py`.
- Downstream: the operator annunciator consumes `status.message`, not the KeyValue list, so the two new diagnostic keys break no consumer.
- `/diagnostics` is already in both bizzyboat record lists, so that half of the plan's recorded consequence is satisfied; `serial_tap` is the half that is not.

## Implementation
**Status**: complete
**When**: 2026-09-15 09:09 -04:00
**By**: Claude Code Agent (Claude Opus)

**Branch**: feature/issue-77 at `94f9f09`
**Addressed**: `## Local Review (Pre-Push)` 2026-09-15 09:04 -04:00, round 1 (changes-requested), at `8f1b254`
**Commits**: `a6e4339`, `f737576`, `94f9f09`

### Actions
- [x] (must-fix) Ordering invariant untested — added `test_serial_tap_publishes_after_parser_feed`, which records parser-feed, primary-`sound_speed`-publish and tap-publish markers per chunk and asserts the exact sequence — `sound_speed_bridge/test/test_node.py` (`a6e4339`). The recorder is a delegating parser whose marker is appended *inside* the generator body, so it timestamps the feed at the moment `_serial_loop` starts consuming it, which is what the invariant is about.
- [x] (must-fix) `serial_tap` absent from the bizzyboat record lists — **deferred to host**: the host files the `unh_echoboats_project11` follow-up at the publish checkpoint and references it in the PR body. Cited sites, for the PR body: `bizzyboat_project11/config/bizzyboat.yaml:813-818` (the `logger`/`sonar_logger` record lists), `:709`, `:919` (prose still describing marine_tools#77 as future work). No change in this repo. (deferred: host-handled)
- [x] (suggestion) `tap_byte_count` under-reported wire traffic — the increment now happens before the publish attempt, so the counter means "bytes read off the wire", which is what the silent-probe question needs; `tap_error_count` still reports failed publishes (per chunk). Docstring, the `/diagnostics` comment, `test_serial_tap_publish_failure_is_counted_not_fatal`'s assertion and plan.md steps 2/3 and the test list updated together — `sound_speed_bridge/sound_speed_bridge/node.py:225-249,367-378`, `sound_speed_bridge/test/test_node.py:355-360`, `.agent/work-plans/issue-77/plan.md` (`f737576`).
- [x] (suggestion) `_handle_reading`'s four publishes have no exception isolation — **deferred**, pre-existing behaviour; no behaviour change in this PR. Follow-up candidate for the host to file — `sound_speed_bridge/sound_speed_bridge/node.py:269-292`. (deferred: pre-existing, out of scope)
- [x] (suggestion) Transport-level-drop caveat wording (RELIABLE + KEEP_LAST(10)) — **deferred** by host decision — `sound_speed_bridge/sound_speed_bridge/node.py:113-125`. (deferred: host decision)
- [x] (suggestion) File the deferred `sound_speed_bridge` README issue — **deferred**: issue filing belongs to the host at the publish checkpoint; this sub-agent files no GitHub issues. (deferred: host decision)
- [x] (suggestion) Knowledge-doc candidate for the "diagnostic publish after the primary path, counted + throttled broad except" idiom — **deferred**, proposal recorded, no edit. (deferred: host decision)

### Verification
- `./sensors_ws/build.sh sound_speed_bridge` then `./sensors_ws/test.sh sound_speed_bridge`:
  `Summary: 51 tests, 0 errors, 0 failures, 0 skipped` (was 50 before this pass).
- Mutation check on the new ordering test: hoisting `self._publish_serial_tap(data)` above the `for reading in self._parser.feed(...)` loop produced `Summary: 51 tests, 0 errors, 1 failure, 0 skipped`, the single failure being `test_serial_tap_publishes_after_parser_feed`. `node.py` restored via `git checkout --` and re-verified green.
- `ament_flake8` / `ament_pep257` are part of the suite and stayed clean; pre-commit hooks ran on every commit (no `--no-verify`).

### Not pushed
No `git push`, no PR, no issues filed — the host performs those.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 09:13 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: approved

**Branch**: feature/issue-77 at `6b656c6`
**Mode**: pre-push
**Depth**: Standard (reason: round-2 re-review of a ~100-line delta — two fix commits, `a6e4339` + `f737576` — on an already-Deep-reviewed branch; fan-out sized to the delta)
**Must-fix**: 0 | **Suggestions**: 1
**Round**: 2 | **Ship**: recommended — both round-1 must-fixes are closed (one fixed and mutation-verified twice independently, one deferred to the host by design), and the only remaining item is a one-line plan-list omission

**Specialists**: Static Analysis (ament_flake8 + ament_pep257, run as part of the package suite — clean), Claude Adversarial Lens A (logic/test-robustness) and Lens B (systemic/consistency/lifecycle), both fresh-context on the round-2 delta. Governance and Plan Drift carried by the lead reviewer at this delta size. Copilot and Local cross-model reads off (not opted in).

### Findings
- [ ] (suggestion) The plan's test list (`plan.md` Approach step 5) records every other test, including the one added during implementation, but not `test_serial_tap_publishes_after_parser_feed` — the plan-first workflow expects the plan to stay in sync with the branch, and the ordering decision it documents (step 2, "Consequence of the ordering choice, recorded") is now guarded by a test the list does not mention; add the one bullet before push so PR review does not flag it as drift — `.agent/work-plans/issue-77/plan.md` Approach step 5

### Round-1 must-fixes — status
- **Must-fix 1 (ordering invariant untested): closed.** `test_serial_tap_publishes_after_parser_feed` (`test/test_node.py:399-448`) records a per-chunk marker sequence and asserts it exactly. It is robust, not incidental: the `feed` marker is appended *inside* `_OrderRecordingParser.feed`'s generator body, so it is timestamped when `_serial_loop` starts consuming the generator rather than when the generator object is built; and if the parser ever yielded nothing the assertion would still fail under the mutation, so the guard does not depend on a successful parse.
- **Must-fix 2 (cross-repo bizzyboat.yaml record-list gap): still open by design** — deferred to the host at the publish checkpoint (file the `unh_echoboats_project11` follow-up and cite it in the PR body). Not actionable in this repo; not counted against this round.

### Verification performed by the lead reviewer
- Suite: 51/51 green before and after (`./sensors_ws/test.sh sound_speed_bridge`). `ament_flake8` / `ament_pep257` run inside the suite and are clean. Worktree left clean (`git status` empty).
- **Mutation, run independently in an out-of-tree copy** (so the worktree was never modified): hoisting `self._publish_serial_tap(data)` above the `for reading in self._parser.feed(...)` loop fails exactly one test — `test_serial_tap_publishes_after_parser_feed`, at index 0 of the sequence — with all other node tests green. Lens A reproduced the same mutation independently in-tree and reverted it; both reads agree.
- Parser trace (`AMLParser.feed`): `b'1500.123\r\r\n'` frames one reading and leaves an empty buffer (the trailing `\r` frames an empty sentence, which is skipped, and the `\n` is stripped as padding), so the test's "one feed + one sound_speed per chunk" expectation is a property of the framer, not a coincidence, and no buffer state crosses the chunk boundary.
- `tap_byte_count` semantics: the increment sits *after* the `_stop_event` guard and *before* the publish, so a failing publish now reads as "bytes arrived, publishes failed" (the silent-probe question the counter exists to answer) while post-shutdown chunks are still uncounted — `test_serial_tap_noop_after_stop`'s `== 0` assertion is therefore still correct and needed no change. Every site describing the counters agrees: the `_publish_serial_tap` docstring (`node.py:231-237`), the `_publish_diagnostics` comment (`node.py:368-376`), `test_serial_tap_publish_failure_is_counted_not_fatal` (`test/test_node.py:355-360`), and `plan.md` steps 2/3 and the test description. The three success-path tap tests assert the same totals under both old and new semantics, so their silence is correct rather than stale.
- Thread-safety unchanged: both counters remain single-writer (serial thread) plain ints read by the diagnostics timer — the same unlocked pattern as `_parse_error_count` / `_serial_reconnect_count`, whose rationale the plan already records. Reordering an increment past a `try` changes nothing there.
- New test harness introduces no lifecycle hazard: it reuses `_make_node` / `_drive_serial_loop` (loop driven synchronously on the test thread, no live serial thread), swaps mocks only after `_make_node` has validated the real publishers' topic names/types, and destroys the node in a `finally`.

### Adjudicated in round 1 — not re-raised
RELIABLE QoS choice; the deliberately broad `except`; `parsers.py`'s unbounded accumulation buffer (tracked as marine_tools#78); `_handle_reading`'s lack of exception isolation (pre-existing, follow-up candidate); the transport-drop caveat wording; the `sound_speed_bridge` README; the knowledge-doc idiom candidate. All carry recorded reasons; no new evidence surfaced against any of them this round.

## Implementation
**Status**: complete
**When**: 2026-09-15 09:33 -04:00
**By**: Claude Code Agent (Claude Opus)

**Branch**: feature/issue-77 at `ae6139a`
**Addressed**: operator scope change at the publish checkpoint (not a review round) — the approved branch published the tap unconditionally
**Commits**: `b33471f` (feature), `16b6a55` (tests), `ae6139a` (plan sync)

### Operator decision driving this pass

> "I'm not sure I want this enabled all the time, so let's default to not
> enabled, make sure it can get enable on the fly, and not worry about writing
> to a bag yet."

and, on reviewing the first cut of that (which kept the publisher advertised
while disabled so a recorder could attach early):

> "Why is the topic published when it's not enabled? I'd rather it only publish
> if we enable it."

Both are recorded verbatim in `plan.md` Design Decision 5.

### Actions
- [x] Added the `serial_tap_enabled` parameter, **default `false`**, declared with a `ParameterDescriptor` whose `read_only=False` is stated explicitly (rclpy's only descriptor-level "settable at runtime" marker) and whose `description` names the exact `ros2 param set` command — `sound_speed_bridge/sound_speed_bridge/node.py` (`b33471f`)
- [x] Registered `add_on_set_parameters_callback(self._on_set_parameters)`, before the serial thread starts, so a set applies to the running reader: `ros2 param set /sound_speed_bridge serial_tap_enabled true` takes effect on the next serial chunk with no restart — restarting to enable a diagnostic would drop the very stream being diagnosed and reset the counters that frame the question
- [x] **No publisher and no topic while disabled** (the operator's correction): `_set_tap_publishing()` creates the publisher on true and `destroy_publisher`s it on false, so `ros2 topic list` shows `serial_tap` exactly when the tap runs. Enabled state is a **property derived from the publisher reference**, not a parallel bool that could disagree with it. `__init__` routes a launch-time `serial_tap_enabled: true` through the same helper, so "enabled at startup" and "enabled later" are one code path. Idempotent, so a repeated set never tears down a live topic under a recorder
- [x] Thread-safety, documented at `_set_tap_publishing`: the publisher reference is swapped under a **dedicated `_tap_lock`** — deliberately not `self._lock`, which is held on the primary reading path (`_handle_reading`) and by the diagnostics timer, so reusing it would let a diagnostic contend with the `SoundSpeed` path, the exact coupling this file's ordering rule exists to prevent. The serial thread copies the reference under the lock and publishes outside it. The one remaining race — publishing on a reference taken just before a disable — is already covered by the existing broad `except` + `tap_error_count`: `Publisher.publish` holds a use-count that defers destruction and otherwise raises `InvalidHandle` (a plain `Exception` subclass), so a disable costs at worst one counted, logged tap error
- [x] `tap_byte_count` **keeps counting wire bytes while disabled** — incremented before the gate as well as before the publish. It is the cheap always-on answer to "is the probe silent?", costs one integer add per chunk, and is what tells an operator whether there is anything to enable the tap *for*
- [x] Surfaced `serial_tap_enabled` as a `/diagnostics` KeyValue, and added it to the startup INFO log line. With the tap off by default, an absence of tap messages in a bag would otherwise be ambiguous between "the probe was silent" and "nobody switched the tap on"; `tap_byte_count` separates silent from corrupt, this key separates both from not-enabled
- [x] The callback **validates** the type rather than coercing (`bool('false')` is `True`, so coercion would enable the tap for an operator who asked for the opposite) and logs INFO on every accepted set — including a re-set to the current value, which is an operator asking for exactly that confirmation
- [x] Tests (`16b6a55`), all driving the real set-parameters callback via `node.set_parameters(...)` rather than poking internals: default-disabled publishes nothing / has no publisher / still counts bytes / leaves the primary path untouched; enable-then-publish; the **ROS-graph** view (`get_publisher_names_and_types_by_node`, i.e. what `ros2 topic list` shows) has no `serial_tap` while disabled, has it when enabled, loses it on disable, with `/raw` as the control proving the query answers; off→on→off mid-stream publishes exactly the enabled chunk; repeated enable keeps the same publisher; non-bool rejected at both layers; an unrelated parameter set is accepted and leaves the tap alone; the diagnostics key reads `false` then `true`. Existing tap tests enable the parameter in the `_make_node` fixture (`tap_enabled=True`) — no assertion was weakened
- [x] **Deliberately not filed**, at the operator's request ("not worry about writing to a bag yet"): the `unh_echoboats_project11` follow-up adding `serial_tap` to the deployment bag record list (round-1 review must-fix 2). The cited sites stay in `plan.md` for whenever it is wanted. With the tap off by default, the record-list gap is no longer what makes the tap inert — the parameter is
- [x] **Deliberately not done**: the callback applies only `serial_tap_enabled` and does not start *rejecting* runtime sets of the node's other startup-only parameters the way `garmin_sidescan`'s node does. That is a behaviour change well beyond this issue; accepting them is what rclpy already did before any callback existed. Recorded in `plan.md`, not filed

### Verification
- `./sensors_ws/build.sh sound_speed_bridge` then `./sensors_ws/test.sh sound_speed_bridge`:
  `Summary: 59 tests, 0 errors, 0 failures, 0 skipped` (was 51 before this pass).
- `ament_flake8` / `ament_pep257` run inside the suite and are clean.
- Mutation-verified, each against a file backup (not `git checkout`), each failing exactly the tests that guard it:
  always creating the publisher regardless of the gate → every `_make_node` test;
  counting bytes only while enabled → `test_serial_tap_disabled_by_default_publishes_nothing` + `test_serial_tap_runtime_toggle_takes_effect_mid_stream`;
  skipping the callback's type check → `test_serial_tap_enabled_rejects_non_bool`;
  never destroying the publisher on disable → `test_serial_tap_topic_advertised_only_when_enabled`.
- Probed rclpy's own behaviour rather than assuming it: a wrong-typed `set_parameters` returns `successful=False` with a reason and never reaches the callback — it does **not** raise `InvalidParameterTypeException`. The test asserts what rclpy actually does.
- Pre-commit hooks ran on every commit; no `--no-verify`.

### Consequence accepted
A subscriber cannot attach before the tap is enabled, so the first chunk or
two after enabling may be lost to subscriber/publisher discovery. That is the
operator's explicit trade — a topic that exists only when the tap is on is
worth more than the first ~1 s of bytes after enabling — and the missed bytes
are still counted in `tap_byte_count`.

### Not pushed
No `git push`, no PR, no issues filed — the host performs those.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 09:41 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: changes-requested

**Branch**: feature/issue-77 at `37ee5e9`
**Mode**: pre-push
**Depth**: Deep (reason: the operator's scope change added a dynamic-parameter callback that races the serial thread and creates/destroys a publisher at runtime — concurrency/lifecycle in a sensor driver; +272 lines in `node.py`. Promoted further by the handoff's flag that `node.py` was reconstructed from edit scripts after an accidental `git checkout --`, so nothing from rounds 1-2 could be assumed to have survived)
**Must-fix**: 2 | **Suggestions**: 3
**Round**: 3 | **Ship**: recommended — both must-fixes are mechanical with an obvious correction each (one missing test; one `try/except` plus its test), neither is a design question, and neither needs another independent read to settle. Round 2's zero must-fixes was a verdict on the *pre-scope-change* diff, so 0 → 2 is this code's first adversarial read, not a diverging loop. Address both, re-verify the suite, push.

**Specialists**: Static Analysis (ament_flake8 + ament_pep257, run inside the package suite — clean), Governance, Plan Drift, Claude Adversarial Lens A (logic/correctness) and Lens B (systemic/concurrency/lifecycle), both fresh-context. Copilot and Local cross-model reads off (not opted in). The lead reviewer re-ran the full suite and re-ran five mutations independently against an out-of-tree copy.

### Findings
- [x] (must-fix) The launch-time enable path is unguarded by any test: replacing `__init__`'s `self._set_tap_publishing(bool(self.get_parameter('serial_tap_enabled').value))` with `pass` leaves all 19 node tests green. Every test constructs the node with the default and enables afterwards through `set_parameters`, so the plan's and the code comment's central claim — "enabled at startup and enabled later are one code path" — is asserted by prose only. A regression would ship a boat where `serial_tap_enabled:=true` in the launch file silently advertises nothing. Add a test that constructs the node with the parameter already true (e.g. re-init the context inside the test with `rclpy.init(args=['--ros-args', '-p', 'serial_tap_enabled:=true'])`, since `SoundSpeedBridgeNode.__init__` forwards no `parameter_overrides`) and asserts `_tap_pub` exists and `/serial_tap` is advertised before any `set_parameters` call — `sound_speed_bridge/sound_speed_bridge/node.py:185-186`, `sound_speed_bridge/test/test_node.py`
- [x] (must-fix) A `create_publisher` / `destroy_publisher` failure inside the parameter callback kills the whole bridge, not just the tap: `rclpy`'s `_set_parameters_atomically_common` wraps the on-set callbacks in no `try`, `parameter_service.py` catches only `ParameterNotDeclaredException`, and `executors.py:917-918` re-raises a handler exception straight out of `rclpy.spin()`, which `main()` guards only for `KeyboardInterrupt`. So an RMW/resource failure during a live `ros2 param set serial_tap_enabled true` takes down the primary SoundSpeed/Temperature/FluidPressure publishing — the exact outcome `_publish_serial_tap`'s deliberately broad `except` exists to prevent on the serial-thread side ("a diagnostic must not be able to kill the sensor"). The guard is missing on the parameter-set side. Wrap the create/destroy in `try/except` and return `SetParametersResult(successful=False, reason=...)` so the failure degrades to a rejected `ros2 param set`; add a test that makes `create_publisher` raise and asserts the node survives with an unsuccessful result — `sound_speed_bridge/sound_speed_bridge/node.py:278-294`, `:426-427`
- [x] (suggestion) `_set_tap_publishing`'s enable branch reads `self._tap_pub` outside `_tap_lock` before acting on it. That check-then-act is safe **only** because `main()` uses a single-threaded `rclpy.spin()`, so the callback can never run on two threads at once; under a `MultiThreadedExecutor` two concurrent enables could each create a publisher and leak the loser, invisibly to a test suite that drives everything from one thread. The docstring explains the lock's scope but never states the single-executor-thread invariant the unlocked read depends on — one line would keep a future executor change from silently reintroducing the leak — `sound_speed_bridge/sound_speed_bridge/node.py:278-280`
- [x] (suggestion) The `sound_speed_bridge` README gap, deferred since PR#76, has widened: this change adds a second raw-adjacent topic **and** the node's first runtime-settable parameter, with operational semantics (off-by-default, the accepted subscriber-discovery lag after enabling, three tap diagnostics keys) that an operator now reconstructs from source, the startup log line and `ros2 param describe`. Operator's call whether to file it — the plan already names it as a candidate; what is owed is the yes/no, not the README — `.agent/work-plans/issue-77/plan.md` Design Decision 4
- [x] (suggestion) Knowledge-doc candidate, operator's call, proposal only and no auto-edit: the idiom now used twice in this node — a diagnostic publish placed after the primary data path, wrapped in its own broad `except`, with failures **counted** so a silent catch cannot hide a broken diagnostic — is reusable across this workspace's serial/UDP bridge nodes. Candidate home `.agent/knowledge/ros2_development_patterns.md`

### Reconstruction check — did the round-1/round-2 properties survive?
Yes. `node.py` was read end to end and is internally coherent; each previously-established property was re-verified by mutation against an out-of-tree copy (`cp` of the package tree, never `git checkout --`; the worktree stayed clean throughout), each failing exactly the test that guards it and nothing else:
- publish-after-feed ordering (`node.py:308-315`) — hoisting `_publish_serial_tap(data)` above the feed loop fails only `test_serial_tap_publishes_after_parser_feed`
- shutdown guard (`node.py:366-367`) — deleting it fails only `test_serial_tap_noop_after_stop`
- exception isolation (`node.py:377-382`) — deleting the `try/except` fails only `test_serial_tap_publish_failure_is_counted_not_fatal`
- byte-count semantics (`node.py:368`, incremented after the stop guard but before both the enabled gate and the publish) — verified by reading and by the disabled-path tests
- publisher teardown on disable (`node.py:291-294`) — never destroying it fails only `test_serial_tap_topic_advertised_only_when_enabled`
The fifth mutation is must-fix 1: deleting the launch-time enable call fails **nothing**.

### Verification performed by the lead reviewer
- Full suite from the worktree (`./sensors_ws/build.sh` then `./sensors_ws/test.sh sound_speed_bridge`): `Summary: 59 tests, 0 errors, 0 failures, 0 skipped`. `ament_flake8` / `ament_pep257` run inside it and are clean. Worktree left clean.
- Out-of-tree baseline for the mutation work: 19 node tests, all passing, restored and re-verified green after every mutation.
- The launch-time path itself **works** — probed by constructing the node under `rclpy.init(args=['--ros-args', '-p', 'serial_tap_enabled:=true'])`: the publisher exists as `/serial_tap` immediately after construction. Must-fix 1 is a coverage gap, not a defect.
- Lens B's `InvalidHandle` claim re-checked against the installed rclpy rather than the code comment: `Publisher.publish` enters the use-counted handle and `InvalidHandle` is a `RuntimeError` subclass, so the publish-racing-a-disable path really is covered by the broad `except`. The comment is accurate.
- Must-fix 2's propagation chain read directly in `/opt/ros/jazzy/lib/python3.12/site-packages/rclpy/` (`node.py` `_set_parameters_atomically_common`, `parameter_service.py`, `executors.py:917-918`) — not taken on the specialist's word.

### Adjudicated this round — recorded so they are not re-raised
- "Applying the side effect in a **pre**-set callback can diverge from the parameter store (Jazzy offers `add_post_set_parameters_callback`)": **not a defect here.** rclpy commits `self._parameters[...]` only after every on-set callback returns success, and `add_on_set_parameters_callback` **inserts at index 0**, so any callback registered later runs *before* this one — a rejection by another callback can therefore never land after this one has acted. This node registers exactly one callback. The one way the ordering does bite is an *exception*, which is must-fix 2.
- Parameter set racing `destroy_node()`: unreachable — `main()` calls `destroy_node()` only after `rclpy.spin()` returns on the same thread.
- `__init__` ordering (`_set_tap_publishing` and the callback registration before `_stop_event` and the thread start): no window — nothing can reach the callback until `__init__` returns and something spins.
- Stale publisher at shutdown: none — `super().destroy_node()` destroys every registered publisher, `_tap_pub` included.

### Adjudicated in earlier rounds — not re-raised
RELIABLE QoS choice; the deliberately broad `except`; `parsers.py`'s unbounded accumulation buffer (marine_tools#78); `_handle_reading`'s lack of exception isolation (pre-existing follow-up candidate — note must-fix 2 is a *different* path and is in scope); the transport-drop caveat wording; the cross-repo `unh_echoboats_project11` bag record-list follow-up (deliberately not filed at the operator's "not worry about writing to a bag yet"). Governance found no must-fix and no ADR non-compliance; Plan Drift found none — the plan's test list, Design Decision 5 and every repointed stale comment match the diff.

## Implementation
**Status**: complete
**When**: 2026-09-15 09:48 -04:00
**By**: Claude Code Agent (Claude Opus)

**Branch**: feature/issue-77 at `a2a55d6`
**Addressed**: ## Local Review (Pre-Push), round 3 (When 2026-09-15 09:41 -04:00, reviewed `37ee5e9`), verdict changes-requested — 2 must-fix, 3 suggestions
**Commits**: `215d7af`, `4e52b31`, `0d2cdf6`, `a2a55d6`

### Actions
- [x] (must-fix) Launch-time enable path untested — added `test_serial_tap_enabled_at_launch_needs_no_runtime_set`: re-inits the context with `rclpy.init(args=['--ros-args', '-p', 'serial_tap_enabled:=true'])` (the node forwards no `parameter_overrides`), then asserts `_tap_pub` exists with the `/serial_tap` name/type contract and publishes the first chunk with no `set_parameters` call anywhere — `sound_speed_bridge/test/test_node.py` (commit `215d7af`)
- [x] (must-fix) A publisher create/destroy failure in the parameter callback would propagate out of `rclpy.spin()` and kill the bridge — the call is now wrapped in `try/except Exception`, logged at ERROR with the exception and returned as `SetParametersResult(successful=False, reason=...)`, so the failure degrades to a rejected `ros2 param set` and the parameter keeps its previous value. State is consistent either way and now documented: a failed create never assigns `_tap_pub`; a failed destroy leaves the reference **dropped, not restored**, because rclpy removes the publisher from the node's registry before `destroy()` can raise — restoring it would hand the serial thread a publisher nothing owns and `destroy_node()` would no longer clean up — so the tap reads as off (which it is) while the rejected set leaves the store saying otherwise, and the `serial_tap_enabled` diagnostic, derived from the publisher, reports the reality. Covered by `test_tap_enable_publisher_failure_is_rejected_not_fatal` and `test_tap_disable_publisher_failure_is_rejected_not_fatal` — `sound_speed_bridge/sound_speed_bridge/node.py` `_on_set_parameters` + `_set_tap_publishing`, `sound_speed_bridge/test/test_node.py` (commit `4e52b31`)
- [x] (suggestion) Single-executor-thread invariant behind the unlocked check-then-act — stated as a comment at the check itself, naming the MultiThreadedExecutor leak it would become and the remedies (a dedicated mutation lock, or a mutually-exclusive callback group). Holding `_tap_lock` across the create/destroy was considered and **rejected**, and the comment says so: it would put an RMW publisher create/destroy on the serial reader's critical path (`_publish_serial_tap` takes the same lock), the exact coupling this lock was split off from `self._lock` to avoid — `sound_speed_bridge/sound_speed_bridge/node.py` `_set_tap_publishing` (commit `0d2cdf6`)
- [x] (suggestion) `sound_speed_bridge` README gap — **(deferred: operator decision, per the host's instruction for this pass; what is owed is the yes/no, not the README. The plan already carries it as a candidate and the review entry records the widened case.)**
- [x] (suggestion) Knowledge-doc candidate (diagnostic-after-primary-path + broad `except` + counted failures, for `.agent/knowledge/ros2_development_patterns.md`) — **(deferred: operator's call, proposal only and explicitly no auto-edit; recorded here and in the round-3 review entry.)**

### Verification
- Full suite from the worktree (`source setup.bash && ./sensors_ws/build.sh sound_speed_bridge && ./sensors_ws/test.sh sound_speed_bridge`): `Summary: 62 tests, 0 errors, 0 failures, 0 skipped` (was 59; +3). `ament_flake8` / `ament_pep257` run inside it and are clean.
- Mutation verification, on an out-of-tree copy of the package (`cp` into the scratchpad; never `git checkout --`; the worktree stayed clean and the baseline was re-verified green after each mutation, 22 node tests):
  - `__init__`'s `self._set_tap_publishing(bool(self.get_parameter('serial_tap_enabled').value))` → `pass`: **1 failed, 21 passed** — exactly `test_serial_tap_enabled_at_launch_needs_no_runtime_set`, which is the mutation the round-3 review reported as killing nothing.
  - The callback's `try/except` removed (bare `self._set_tap_publishing(requested)`): **2 failed, 20 passed** — exactly `test_tap_enable_publisher_failure_is_rejected_not_fatal` and `test_tap_disable_publisher_failure_is_rejected_not_fatal`.
- `plan.md` synced in the same pass: the three new tests with their mutation results in the Design-Decision-5 test list, plus two new paragraphs — the callback's reject-don't-die failure handling with the drop-the-reference choice, and the single-executor-thread invariant (commit `a2a55d6`).

### Next step
Lifecycle: **Implementation** → **review-code** (re-review the fixes). Nothing was pushed and no PR exists; the host drives the next phase.

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-09-15 10:07 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: approved

**Branch**: feature/issue-77 at `47fcf09`
**Mode**: pre-push
**Depth**: bounded re-check (scoped by the host to the round-3 must-fix closures, the failed-destroy state decision, what the four fix commits introduced, and plan drift — not a fresh full-diff read)
**Must-fix**: 0 | **Suggestions**: 3
**Round**: 4 | **Ship**: recommended — both round-3 must-fixes independently re-verified closed by mutation, the one design decision in the fix pass is sound and self-healing, and nothing new was introduced; the three remaining items are all non-blocking polish.

**Specialists**: lead-reviewer mutation + empirical verification against installed rclpy; one fresh-context Claude adversarial pass (sonnet, proportionate fan-out per the host's instruction). Static analysis ran inside the package suite (`ament_flake8` / `ament_pep257`, clean). Copilot and local cross-model reads off (not opted in).

### Findings
- [ ] (suggestion) `__init__`'s `_set_tap_publishing` call is unguarded, so a `create_publisher` failure at launch aborts node construction — the asymmetry the callback's new guard created. Failing loud at startup is defensible, but the adjacent comment's "enabled at startup and enabled later reach the identical state rather than being two code paths" is now true for success and not for failure; half a sentence would close it — `sound_speed_bridge/sound_speed_bridge/node.py:185-186`
- [ ] (suggestion) `test_tap_disable_publisher_failure_is_rejected_not_fatal` does not assert `get_parameter('serial_tap_enabled').value is True` after the rejected disable, so the one deliberate inconsistency in this design — store says true, derived diagnostic says false — is documented in three places and asserted nowhere; its sibling enable test does pin its store value — `sound_speed_bridge/test/test_node.py:846-874`
- [ ] (suggestion) The launch test duplicates `_make_node`'s thread-stop/clear dance inline because `_make_node` cannot take launch-time parameter overrides. Maintainability nit, no correctness impact — `sound_speed_bridge/test/test_node.py:762-786`

### (a) Round-3 must-fix closures — both genuinely closed
Both mutations the implementer reported were re-run independently by the lead reviewer against an out-of-tree copy (`cp` into the scratchpad; the worktree was verified clean before and after and no `git checkout --` / `git restore` was used), with the 22-test baseline re-verified green after each:
- `__init__`'s `self._set_tap_publishing(bool(self.get_parameter('serial_tap_enabled').value))` → `pass`: **1 failed, 21 passed** — exactly `test_serial_tap_enabled_at_launch_needs_no_runtime_set`. This is the mutation that killed nothing at round 3.
- The callback's `try/except` removed (bare `self._set_tap_publishing(requested)`): **2 failed, 20 passed** — exactly `test_tap_enable_publisher_failure_is_rejected_not_fatal` and `test_tap_disable_publisher_failure_is_rejected_not_fatal`. The adversarial pass reproduced this independently and confirmed by traceback that the `RuntimeError` propagates straight out of `set_parameters()` through rclpy's `_set_parameters_atomically_common` when the guard is removed — so the guard is load-bearing, not vacuous.

The new launch test was additionally checked for the one thing that could make it a false pass — the `rclpy.shutdown()` / `rclpy.init(args=['--ros-args', '-p', 'serial_tap_enabled:=true'])` dance inside the test body, against the autouse `_ros_context` fixture. It is order-independent: passes alone, passes when run first ahead of other tap tests, passes in the full file. The adversarial pass traced `shutdown()` setting `g_default_context = None` in `rclpy/utilities.py` (so the next `init()` allocates a fresh `Context`) and ran the test 10x under `pytest-repeat`. No global-state leak between tests.

### (b) The failed-destroy state decision — sound, and documented
The load-bearing premise was verified directly in the installed rclpy (`/opt/ros/jazzy/.../rclpy/node.py`): `destroy_publisher` does `self._publishers.remove(publisher)` **before** `publisher.destroy()` and swallows only `InvalidHandle`, so "rclpy has already removed the publisher from the node's registry by then" holds for every exception that can reach this code — restoring `_tap_pub` really would hand the serial thread a publisher nothing owns. The mirror case also checks out (adversarial, read in rclpy): `create_publisher` appends to `_publishers` only after full success and tears the handle down on any exception, so a failed create leaves `_tap_pub` None with no partial registration. The residual rcl-handle leak after a failed destroy is inherent to rclpy's remove-then-destroy ordering, not created by this choice.

The documented divergence was additionally probed on a live node to confirm it is not a trap — it is self-healing in both directions:
- failed disable → `successful=False`, store `True`, derived `False` (exactly as documented)
- retry the disable → `successful=True`, store `False`, derived `False` (converges)
- enable from the diverged state → `successful=True`, store `True`, derived `True` (converges)
Favouring "the diagnostic reports reality" over "the parameter store reports reality" is the right call given the constraint, and it is stated plainly in the code comment, the docstring and the plan rather than glossed over.

### (c)/(d) Newly introduced, and plan drift
Nothing broken by the four commits. `0d2cdf6` is comment-only and accurately describes the code (`_set_tap_publishing`'s only callers are `__init__` and the set-parameters callback, both on the single `rclpy.spin()` thread). `# noqa: B902` at `node.py:466` matches the pre-existing idiom at `node.py:407`. `create_publisher` is called with no `qos_overriding_options`, so a failed create declares no stray parameter that could break a later retry (adversarial, checked in `qos_overriding_options.py`). Plan drift: **none** — `a2a55d6` adds the callback failure-handling paragraph, the single-executor-thread invariant paragraph and the three new tests with their mutation results, all matching the diff; the README deferral is still carried in the plan's consequences map as a deliberate "no".

### Verification performed by the lead reviewer
- Full suite from the worktree (`source setup.bash && ./sensors_ws/build.sh sound_speed_bridge && ./sensors_ws/test.sh sound_speed_bridge`): `Summary: 62 tests, 0 errors, 0 failures, 0 skipped`. `ament_flake8` / `ament_pep257` run inside it and are clean.
- Out-of-tree baseline 22 node tests green; both round-3 mutations re-run and restored; order-independence runs; the live divergence/recovery probe above. Worktree left clean (`git status --porcelain` empty).
- `marine_tools` has no `.agents/` directory at all, so there is no verified-parameter table to update for `serial_tap_enabled` — a pre-existing repo-level gap, out of scope for this bounded round and unchanged by this diff.

### Not re-litigated (adjudicated or deferred in rounds 1-3, with recorded reasons)
The deliberately broad `except` in `_publish_serial_tap`; the RELIABLE QoS choice; `parsers.py`'s unbounded accumulation buffer (marine_tools#78); `_handle_reading`'s lack of exception isolation; pre-set vs post-set parameter-callback placement; the `sound_speed_bridge` README (operator's yes/no is what is owed, not the README); the knowledge-doc candidate for `.agent/knowledge/ros2_development_patterns.md` (operator's call, proposal only); the cross-repo `unh_echoboats_project11` bag record-list follow-up (host-handled at the publish checkpoint).

### Next step
Lifecycle: **Local Review** (approved) → push / open PR → **triage-reviews**. Nothing was pushed and no PR exists; the host drives the next phase. The three suggestions are non-blocking and can be applied before the push or carried into the PR.
