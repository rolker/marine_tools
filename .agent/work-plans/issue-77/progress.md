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
