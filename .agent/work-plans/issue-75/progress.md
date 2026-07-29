---
issue: 75
---

# Issue #75 — Publish raw framing bytes on ~/raw topic from sound_speed_bridge

## Issue Review
**Status**: complete
**When**: 2026-07-28 00:00 +0000
**By**: Claude Code Agent (Claude Sonnet)

**Issue**: #75
**Comment**: (best-effort post follows this entry; not recorded inline)
**Scope verdict**: well-scoped

### Actions
- [ ] Settle message type in plan: `std_msgs/UInt8MultiArray` (no header/stamp) vs. a new stamped `marine_interfaces` message — if a new msg is introduced, `package.xml`, CMakeLists.txt, and any docs must be updated in the same PR (ADR-0008).
- [ ] Tests must cover both parse-success and parse-failure paths for the raw publisher, per issue request and marine_tools quality standard.

## Plan Authored
**Status**: complete
**When**: 2026-07-28 12:00 +0000
**By**: Claude Code Agent (Claude Sonnet)

**Plan**: `.agent/work-plans/issue-75/plan.md` at `a7fabb8`
**Branch**: feature/issue-75 at `a7fabb8`
**Phases**: single

### Open questions
- [ ] No open questions — plan is review-plan-ready.

## Plan Review
**Status**: complete
**When**: 2026-07-28 18:31 +0000
**By**: Claude Code Agent (Claude Opus)

**Plan**: `.agent/work-plans/issue-75/plan.md` at `a7fabb8`
**PR**: PR-less (--issue 75, layer worktree `feature/issue-75`)
**Verdict**: changes-requested

<!-- Independent review: fresh-context Opus sub-agent. The author By line reads
"Claude Code Agent (Claude Sonnet)"; the agent-name prefix collides (all agents
share "Claude Code Agent"), but this is NOT an in-context author self-review
(different model, fresh context, dispatched as independent reviewer), so the
self-review annotation is intentionally omitted. -->

### Findings
- [ ] (must-fix) Topic name `~/raw` is inconsistent with this node's own publishers and the plan's rationale for it is factually wrong — this node publishes `sound_speed`, `temperature`, `fluid_pressure` as **bare relative** names (namespace-scoped), so `~/raw` (a private name) resolves to `<ns>/sound_speed_bridge/raw`, nested one level below its sibling data topics rather than beside them. The plan's parenthetical "(relative, resolves via node namespace like the others)" is incorrect. The garmin precedent uses `~/` for *all* its topics, so it is internally consistent; copying only garmin's topic name without its surrounding convention creates the inconsistency here. Because the topic name is an external contract (BizzyBoat bag-record list, unh_echoboats_project11#396), settle it deliberately before implementing — recommend `raw` (bare relative) to sit as a sibling of `sound_speed`. Correct the plan's rationale either way. — `plan.md:32`
- [ ] (suggestion) Test approach mismatches the actual node-test precedent in this package family. The plan proposes "patching `rclpy.node.Node` methods at import time," but the established pattern is `zda_serial_bridge/test/test_node.py`: a live `rclpy.init()`/`shutdown()` autouse fixture, `@patch('...node.serial.Serial')`, instantiate the **real** `SoundSpeedBridgeNode()`, then swap the target publisher with a MagicMock (`node._raw_pub = MagicMock()`) and assert on `publish.call_args`, wrapped in `try/finally: node.destroy_node()`. Follow that pattern and cite it. Note `SoundSpeedBridgeNode.__init__` starts a real serial thread (`node.py:115-117`); set the patched serial mock's `read` to return `b''` so the background loop spins harmlessly instead of feeding a MagicMock into `parser.feed()` and raising a thread traceback. — `plan.md:40`
- [ ] (suggestion) Publish idiom: garmin uses `UInt8MultiArray(data=payload)` with `payload` as `bytes` directly (`garmin_sidescan/node.py:709`). The plan's `data=list(reading.raw_bytes)` also works but boxes each byte into a Python int; `data=bytes(reading.raw_bytes)` matches garmin and is cheaper. Minor. — `plan.md:35`

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-07-28 19:38 +0000
**By**: Claude Code Agent (Claude Opus)
**Verdict**: approved

**Branch**: feature/issue-75 at `5c316cd`
**Mode**: pre-push
**Depth**: Light (reason: small, single-package, low-risk feature; no security/cross-layer/governance changes)
**Must-fix**: 0 | **Suggestions**: 1
**Round**: 1 | **Ship**: recommended — no must-fix findings; static analysis clean, both new tests pass (42 passed in implementer run), plan and prior plan-review findings fully honored

### Findings
- [ ] (suggestion) Tests swap `node._raw_pub` for a MagicMock, so the real `publish()` transport path isn't exercised (construction + `bytes(msg.data)` round-trip is). Matches the endorsed `zda_serial_bridge` test precedent — acceptable as-is; a subscription round-trip test would be a nicety, not a correction. — `sound_speed_bridge/test/test_node.py:46,71`

## Integrated Review
**Status**: complete
**When**: 2026-07-29 08:00 -04:00
**By**: Claude Code Agent (Claude Opus)

**PR**: #76 at `f419337`
**Sources**: 3 (Copilot R1 @ `f419337`, Local Review (Pre-Push) @ `5c316cd`, CI rollup @ `f419337`)
**Cross-source confirmations**: 0
**CI**: all-pass (build-and-test success, copilot-pull-request-reviewer success)

### Findings
- [x] (low, Copilot R1) Test serial mock busy-spins: `port.read.return_value = b''` returns instantly, so `_serial_loop`'s inner `while` loop spins at full CPU for the node's lifetime in each test (real `serial.Serial(..., timeout=1.0)` would block). Tests call `_handle_reading()` directly and never need the thread. Fix: after constructing the node in `_make_node`, stop the thread deterministically — `node._stop_event.set(); node._serial_thread.join(timeout=2.0)` — and update the docstring, which currently states the `b''` return is the mechanism for keeping the thread idle. Note this contradicts the earlier `## Plan Review` suggestion (plan.md:40) that prescribed `read -> b''`; the stop-the-thread form supersedes it. — `sound_speed_bridge/test/test_node.py:27-38`
- [x] (suggestion, Local Review (Pre-Push) @ `5c316cd`) (deferred: endorsed `zda_serial_bridge` precedent does the same; review itself rates it a nicety, not a correction) Carried forward, non-blocking: tests swap `node._raw_pub` for a MagicMock, so the real publish transport path isn't exercised. Endorsed `zda_serial_bridge` precedent does the same; a subscription round-trip test would be a nicety, not a correction. — `sound_speed_bridge/test/test_node.py:46,71`

### False positives
- (none)
