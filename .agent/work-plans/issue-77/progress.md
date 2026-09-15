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
- [ ] (must-fix) Ordering invariant "tap publishes after the parser feed" is documented as load-bearing but no test guards it — moving the publish before `_parser.feed()` leaves all 50 tests green (verified empirically by mutation); assert relative call order between the primary publishes and the tap — `sound_speed_bridge/sound_speed_bridge/node.py:203-208`, `sound_speed_bridge/test/test_node.py`
- [ ] (must-fix) Cross-repo consequence is recorded only as plan prose and will evaporate: `serial_tap` is absent from bizzyboat.yaml's `logger`/`sonar_logger` record lists, and bizzyboat.yaml still describes marine_tools#77 as future work — file the unh_echoboats_project11 follow-up and reference it in the PR body, or the tap is inert in the field — `bizzyboat_project11/config/bizzyboat.yaml:813-818,709,919` (cross-repo)
- [ ] (suggestion) `_handle_reading`'s four publishes have no exception isolation at all while the new tap does — same thread, same shutdown path; pre-existing, but this PR draws the contrast, so file it as a follow-up — `sound_speed_bridge/sound_speed_bridge/node.py:269-292`
- [ ] (suggestion) Byte-exactness caveat covers reconnects but not transport: RELIABLE + KEEP_LAST(10) can still drop samples if a subscriber falls more than 10 behind — one sentence in the publisher comment — `sound_speed_bridge/sound_speed_bridge/node.py:113-125`
- [ ] (suggestion) `tap_byte_count` under-reports wire traffic when publishes fail (bytes counted only on success, errors counted per chunk not per byte); consider a bytes-read counter independent of publish success — `sound_speed_bridge/sound_speed_bridge/node.py:236-243`
- [ ] (suggestion) File the deferred `sound_speed_bridge` README (covering `raw` + `serial_tap` + the parameter surface) as a real issue rather than leaving it as plan prose — `.agent/work-plans/issue-77/plan.md` Design Decision 4
- [ ] (suggestion) Knowledge-doc candidate, operator's call: the "diagnostic publish placed after the primary path, wrapped in a counted + throttled-logged broad except" idiom is reusable across this workspace's serial/UDP bridge nodes — proposal only, no auto-edit

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
