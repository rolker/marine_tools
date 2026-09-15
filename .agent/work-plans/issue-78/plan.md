# Plan: parsers: unbounded accumulation buffer — cap + trim WARN

## Issue

https://github.com/rolker/marine_tools/issues/78

## Context

`AMLParser.feed()` and `RegexParser.feed()` in
`sound_speed_bridge/sound_speed_bridge/parsers.py` accumulate incoming
serial bytes into `self._buffer` with no cap. If the configured line
terminator never matches — a misconfigured `regex_line_terminator`, or the
field-observed AML UART collapse under sagging bus voltage (17 BizzyBoat
deployment bags, 2026-09-15: LF read as NUL, so the CRCRLF terminator never
completed for the remaining 1–5 h of each affected day) — the buffer grows
without bound for as long as bytes arrive, and if the terminator later
matches once, the entire accumulated buffer is emitted as a single
multi-MB "sentence" whose `raw_bytes` lands on the RELIABLE `raw` topic
(`node.py:222`), now always-recorded in BizzyBoat's main deployment bag
(rolker/unh_echoboats_project11#396).

Wire rate is bounded (~25 Hz × ~32 B ≈ 800 B/s observed; the issue's
original estimate of ~1 KB/s at 9600 baud is the same order), so a modest
fixed cap comfortably bounds memory without touching legitimate framing.

## Approach

1. **Add a shared buffer-trim helper to `SoundSpeedParser` (the ABC in
   `parsers.py`)** — `_append_and_trim(self, data: bytes) -> None`, called
   by both `AMLParser.feed()` and `RegexParser.feed()` in place of the bare
   `self._buffer += data`. Centralizing avoids duplicating drop-oldest +
   counter logic across the two concrete parsers (`Only what's needed` /
   `Improve incrementally`).
   - Constructor takes `max_buffer_bytes: int` (both `AMLParser.__init__`
     and `RegexParser.__init__` gain this parameter; the ABC stores it and
     initializes `self.buffer_trim_count = 0`).
   - `_append_and_trim` appends `data`, then if `len(self._buffer) >
     max_buffer_bytes`, drops bytes from the **front**, keeping exactly the
     last `max_buffer_bytes` bytes, and increments `self.buffer_trim_count`.
   - **Drop-oldest, tail-preserving**: dropping from the front (not
     discarding the whole buffer, not dropping new incoming bytes) is the
     only choice that keeps the parser able to frame the *next* good
     sentence once the terminator reappears — dropping the tail or the
     whole buffer would either lose the newest (most relevant) data or
     destroy in-flight legitimate data on every trim, not just during a
     stall.
   - Validate `max_buffer_bytes > 0` in the ABC (or each constructor) and
     raise `ValueError` on a bad value, matching the existing
     `regex_line_terminator` validation pattern in `RegexParser.__init__`.

2. **Default: 4096 bytes (4 KiB).** Justification: at ~800 B/s observed
   field rate, 4096 B is ~5 s of continuous unframed data — several times
   longer than any plausible stall-then-recover gap is expected to matter
   for a single trimmed reading, while remaining several ×10 the longest
   legitimate sentence either parser ever frames (AML sentences are a
   handful of ASCII digits, well under 32 B; regex sentences configured so
   far are single-line NMEA-scale text, well under 256 B). A cap in the
   "few KB" range — not bytes-scale, not megabytes-scale — bounds the
   multi-MB hazard the issue describes by roughly three orders of
   magnitude while never truncating a real sentence.

3. **Node parameter**: `declare_parameter('parser_max_buffer_bytes', 4096)`
   in `SoundSpeedBridgeNode.__init__`, declared alongside the existing
   `regex_*` parameters (static — read once at construction, not
   dynamically reconfigurable, matching every other parser-tuning
   parameter in this node). Both `PARSERS` factory lambdas in `parsers.py`
   pass it through:
   ```python
   PARSERS = {
       'aml': lambda node: AMLParser(
           max_buffer_bytes=node.get_parameter('parser_max_buffer_bytes').value),
       'regex': lambda node: RegexParser(
           pattern=node.get_parameter('regex_pattern').value,
           sound_speed_scale=node.get_parameter('regex_sound_speed_scale').value,
           line_terminator=node.get_parameter('regex_line_terminator').value,
           max_buffer_bytes=node.get_parameter('parser_max_buffer_bytes').value,
       ),
   }
   ```

4. **Trim reporting: polled counter, not a callback.** The parser has no
   logger (parsers are pure logic, framing-only), so the WARN must live in
   `node.py`. Expose `buffer_trim_count` as a plain attribute (matching how
   `_publish_diagnostics` already polls `self._rate_hz` and
   `self._parse_error_count` rather than being pushed updates). A callback
   would add an indirection this node doesn't use anywhere else for
   diagnostics.

5. **Throttled WARN, piggybacked on the existing 1 Hz diagnostics timer**
   — no new gate/timer needed. `_publish_diagnostics()` already runs on a
   1 s `create_timer`; track `self._last_buffer_trim_count` (init 0) and in
   `_publish_diagnostics`, compare `self._parser.buffer_trim_count` against
   it. If it increased since the last tick, `self.get_logger().warning(...)`
   once, naming the delta, then update the stored value. This reuses an
   existing cadence instead of introducing a bespoke time-since-last-warn
   gate (the issue-review's "no existing throttled-WARN precedent" note),
   and keeps worst-case log volume at ≤1 WARN/s regardless of how many
   individual trims happen within that second.

6. **Diagnostics counter**: add
   `KeyValue(key='buffer_trim_count', value=str(self._parser.buffer_trim_count))`
   to `_publish_diagnostics`'s `status.values` list in `node.py`, inserted
   after `parse_error_count` (the other parser-health counter) and before
   `udp_send_error_count`. No new topic — matches the operator's recorded
   preference that #78 not add bag volume/noise; this is a counter in the
   existing `/diagnostics` stream only.

7. **Docstrings**: update the module docstring (`parsers.py:1-10`) and the
   `SoundSpeedParser` ABC docstring to describe the cap/drop-oldest
   behavior, alongside the existing framing-quirk documentation (AML's
   CRCRLF quirk, etc.).

8. **Tests**:
   - `sound_speed_bridge/test/test_parsers.py`: new tests for both
     `AMLParser` and `RegexParser` —
     - buffer is bounded: feed far more than `max_buffer_bytes` of
       terminator-free data, assert internal buffer length ≤
       `max_buffer_bytes` and `buffer_trim_count > 0`.
     - drop-oldest / tail-preserving: feed unframed garbage past the cap,
       then feed a well-formed sentence; assert it still frames correctly
       (proves the *tail*, where the new legitimate sentence lands, was
       preserved rather than discarded).
     - no spurious trimming: feed exactly `max_buffer_bytes` (or less) of
       data across multiple `feed()` calls with no terminator; assert
       `buffer_trim_count == 0`.
     - invalid `max_buffer_bytes` (0 or negative) raises `ValueError` at
       construction.
   - `sound_speed_bridge/test/test_node.py`: new test(s) —
     - `buffer_trim_count` KeyValue appears in `/diagnostics` and reflects
       the parser's counter.
     - a WARN is logged when `buffer_trim_count` increases between two
       `_publish_diagnostics()` calls, and *not* logged again on a third
       call where the count is unchanged (proves the throttle, not just
       the WARN itself).

## Branch sequencing (#77 / #78)

**Decision: land #78 independently on `jazzy`, not stacked on
`feature/issue-77`.** Rationale:

- The two issues touch the same `_publish_diagnostics` `KeyValue` list in
  `node.py`, but issue-review verified (and PR #89's own body agrees) the
  overlap is textual only — #77's `serial_tap` sits upstream of
  `parser.feed()`, so there is no design coupling to `#78`'s buffer cap.
- Stacking #78 on an open, unmerged PR (#89) would make #78 unreviewable
  and unmergeable on its own schedule, and would need re-basing again if
  #89's review cycle changes shape before merge.
- Landing independently means #78's PR is reviewable and mergeable
  immediately against `jazzy`, at the cost of a small, mechanical rebase
  conflict in the `KeyValue` list literal whenever #89 merges first (or
  vice versa) — a few-line textual conflict, not a logic conflict.

## Files to Change

| File | Change |
|------|--------|
| `sound_speed_bridge/sound_speed_bridge/parsers.py` | Add `max_buffer_bytes` + `buffer_trim_count` to `SoundSpeedParser` ABC; add `_append_and_trim()` helper; use it in both `AMLParser.feed()` and `RegexParser.feed()`; add `max_buffer_bytes` param to both constructors with validation; update `PARSERS` factories; update module + ABC docstrings |
| `sound_speed_bridge/sound_speed_bridge/node.py` | Add `declare_parameter('parser_max_buffer_bytes', 4096)`; add `self._last_buffer_trim_count = 0`; add trim-delta WARN check in `_publish_diagnostics`; add `buffer_trim_count` `KeyValue` |
| `sound_speed_bridge/test/test_parsers.py` | New tests: buffer bound, drop-oldest/tail-preserving, no spurious trim, invalid cap raises |
| `sound_speed_bridge/test/test_node.py` | New tests: `buffer_trim_count` surfaces in diagnostics; WARN logged once per trim-delta, not repeated when unchanged |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Test what breaks | Step 8 covers cap enforcement, drop-oldest/tail-preservation (the property that actually prevents the multi-MB-sentence hazard), throttle behavior, and counter surfacing — the four things the issue and its review flagged as untested today. |
| A change includes its consequences | `/diagnostics` KeyValue list (node.py) and parser docstrings (parsers.py) both updated in the same PR; no orphaned documentation. |
| Human control and transparency | New parameter is declared, defaulted, and justified (step 2); trims are visible via both a WARN and a polled counter, not silent. |
| Only what's needed | Shared trim logic in the ABC avoids duplicating the same cap/counter code in two parser classes; no speculative generalization (e.g. no per-parser-type configurable trim strategy). |
| Improve incrementally | Single self-contained PR: two source files, two test files, no unrelated refactors. |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| ADR-0008 — ROS 2 conventions | Yes (lightly) | New `parser_max_buffer_bytes` parameter follows the existing `declare_parameter` + constructor-time-validation pattern already used for `regex_line_terminator` (raises `ValueError` at construction on a bad value). |
| ADR-0013 — progress.md entry vocabulary | Yes | This plan and its progress.md entry follow the vocabulary; downstream phases (review-plan, implement, review-code) must continue to. |
| Others (0001–0007, 0009–0012, 0014+) | No | No new tooling, packaging, task-runner, deployment-mode, or CI-verification changes triggered. |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| `SoundSpeedParser` constructor contract (adds `max_buffer_bytes`) | Both concrete parsers' `__init__`, and `PARSERS` factory lambdas | Yes — step 1/3 |
| `_publish_diagnostics`'s `KeyValue` list | Any external consumer of the `/diagnostics` schema | No known consumer breaks — this is an additive `KeyValue`, same pattern as existing counters; no schema version or fixed-index consumer exists in this repo |
| `parsers.py` docstrings (already document framing quirks) | Keep cap behavior documented alongside them | Yes — step 7 |
| `node.py`'s `KeyValue` list ordering | PR #89 (open, touches the same list) | Yes — Branch sequencing section states the merge-order tradeoff explicitly; a rebase conflict is expected and is textual only |

## Documentation & Instruction Impact

- **Stale docs** (must land in this PR): `parsers.py`'s module docstring
  and the `SoundSpeedParser` ABC docstring currently describe buffering
  without mentioning any cap — both become inaccurate once the cap ships,
  so both are updated in step 7.
- **Agent-instruction candidates** (proposals only — operator decides):
  None. The "throttle by piggybacking on an existing periodic timer
  instead of inventing a new gate" pattern (step 5) is a reasonable
  candidate for `.agent/knowledge/ros2_development_patterns.md` if it
  recurs elsewhere, but one instance doesn't yet warrant promoting it —
  flagging here rather than creating it.

## Open Questions

- None — all design points the host flagged (cap default, trim semantics,
  parameter shape, reporting path, WARN placement, branch sequencing,
  tests) are resolved and justified above.

## Estimated Scope

Single PR.
