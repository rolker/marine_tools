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

## Implementation
**Status**: complete
**When**: 2026-07-29 08:04 -04:00
**By**: Claude Code Agent (Claude Opus)

**PR**: #76 at `7be05b3`
**Addressed**: `## Integrated Review` (2026-07-29 08:00 -04:00, PR #76 @ `f419337`)
**Commits**: `7be05b3`

### Actions
- [x] (low, Copilot R1) Test serial mock busy-spin — `_make_node` now sets `node._stop_event`, joins `node._serial_thread` (timeout 2.0s) and asserts it exited, right after construction; docstring corrected to explain that `b''` prevents MagicMock reaching `parser.feed()` but does *not* keep the thread idle. — `sound_speed_bridge/test/test_node.py:27-44`
- [x] (suggestion, Local Review (Pre-Push) @ `5c316cd`) Real publish transport path not exercised — `sound_speed_bridge/test/test_node.py:52,83` (deferred: the endorsed `zda_serial_bridge` test precedent does the same MagicMock swap, and the review itself rates a subscription round-trip test a nicety rather than a correction; adding it is out of scope for this fix pass)

### Verification
- `python3 -m pytest test/ -q` in `sound_speed_bridge`: **42 passed** (1.11s), including flake8/pep257 style tests over the edited file.
- No `.pre-commit-config.yaml` in this repo (no hooks to run); nothing bypassed.

### Next step
Re-review the fixes with a fresh-context sub-agent:
`.agent/scripts/dispatch_subagent.sh --mode in-process --issue 75 --skill review-code`

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-07-29 09:04 -04:00
**By**: Claude Code Agent (Claude Opus)
**Verdict**: changes-requested

**Branch**: feature/issue-75 at `bf752b4`
**Mode**: pre-push
**Depth**: Standard (reason: `plan.md` in diff — project-repo override trigger; code delta 101 lines / 3 files)
**Must-fix**: 2 | **Suggestions**: 7
**Round**: 2 | **Ship**: recommended — both must-fixes are mechanical claim-accuracy corrections at known sites (one in-repo, one owed to echoboats#396); the design question they expose routes to a follow-up issue, not another review round
**Specialists**: Static Analysis (clean, 42 passed incl. flake8/pep257 + xmllint), Claude Adversarial Lens A + Lens B (2 passes), Governance, Plan Drift. Local Adversarial skipped (request timed out, 900s limit). Copilot off (default).
**Round-1 fix verified**: `7be05b3` busy-spin fix is correct — `_stop_event.set()` / bounded join / `assert not is_alive()` is deterministic; Lens A independently confirmed it improves on the `zda_serial_bridge` precedent.

### Findings
- [ ] (must-fix, cross-pass confirmed Lens A + Lens B) `raw` carries **framed sentences only**, not the wire stream, and publishes **nothing** when the stream never frames (wrong baud) — the headline case the feature exists to diagnose. Verified: `parsers.py:88` `lstrip(b'\n')` + `parsers.py:97-98` empty-sentence `continue` drop inter-sentence padding (22 wire bytes in → 18 published for `b'1500.123\r\r\n1499.000\r\r\n'`); terminator-free garbage yields zero readings. Correct the three overstated claim sites to state the limitation. — `sound_speed_bridge/node.py:100-105`, `test/test_node.py:3-6`, `plan.md:12`
- [ ] (must-fix, owed cross-repo consequence of the above) Consumer-side record entry documents the topic as carrying "wrong-baud garbage" and tells operators an absent topic means "an older driver on gabby, not a probe fault" — misleading in exactly the wrong-baud case, where the topic is silent *and* the probe is at fault. Not editable from this worktree; flag for correction under rolker/unh_echoboats_project11#396. — `unh_echoboats_project11/bizzyboat_project11/config/bizzyboat.yaml:572-577`
- [ ] (suggestion, Lens B) Unbounded parser buffer → unbounded `UInt8MultiArray` on a RELIABLE, always-recorded topic. Misconfigured `regex_line_terminator` (field-tunable; BizzyBoat sets `crlf`) grows the buffer ~1 KB/s at 9600 baud, then publishes one multi-MB message into the bag. Root cause pre-existing; this PR routes it onto DDS. File a follow-up: cap the buffer, emit truncated fragment, WARN + diagnostics counter. — `sound_speed_bridge/parsers.py:82,162`
- [ ] (suggestion, Lens A) Move the raw publish after `self._pub.publish(msg)`. `_serial_loop` catches only `(serial.SerialException, OSError)`, so a non-caught exception from the new publish kills the serial thread permanently and leaves `_serial_connected` True — diagnostics then report "No reading for Xs" instead of a fault. A diagnostic-only publisher must not preempt the primary path. — `sound_speed_bridge/node.py:197`
- [ ] (suggestion, Lens A) Tests cannot detect a topic-name or message-type regression — `_raw_pub` is mocked before anything inspects the real publisher, and the topic name is the external contract with echoboats#396. Assert `topic_name` + `msg_type` in `_make_node` before the swap. — `sound_speed_bridge/test/test_node.py:32-49`
- [ ] (suggestion, Lens B) Shutdown race: the best-effort 2 s join can be outlasted by a wedged UART, then `super().destroy_node()` destroys publishers while the daemon serial thread may still be in `_handle_reading` → `InvalidHandle`. `if self._stop_event.is_set(): return` at the top of `_handle_reading` closes it for all four publishers. — `sound_speed_bridge/node.py:188,305-312`
- [ ] (suggestion, Lens B) `_make_node` constructs the node outside the tests' `try/finally`; if its `assert not is_alive()` fires the node is never destroyed and a live thread leaks into the next test's context. Convert to a pytest fixture with teardown. — `sound_speed_bridge/test/test_node.py:32-49,53,74`
- [ ] (suggestion, Lens A) `test_raw_publishes_on_parse_success` doesn't pin `_parse_error_count == 0` — one line pins the `math.isnan` branch from both sides. — `sound_speed_bridge/test/test_node.py:64`
- [ ] (suggestion, lead) No package README, so the new `raw` topic — an external contract — is documented only in a code comment; `garmin_sidescan/README.md` carries a topic table. Pre-existing gap. — `sound_speed_bridge/`

### Plan adherence
No drift. Files changed match plan.md's "Files to Change" table exactly; steps 1-4 implemented as written, including both prior plan-review corrections. If the raw publish is moved (suggestion above), update plan step 3's "before the SoundSpeed publish" wording to match.

### Next step
Dispatch `address-findings` for the open items above, then re-run `review-code`; do not push until a pre-push review comes back approved.
`.agent/scripts/dispatch_subagent.sh --mode in-process --issue 75 --skill address-findings`
