# Plan: sound_speed_bridge byte-stream tap from _serial_loop for wrong-baud diagnosis

## Issue

https://github.com/rolker/marine_tools/issues/77

## Context

`sound_speed_bridge/sound_speed_bridge/node.py`'s `_serial_loop` currently does:

```python
data = ser.read(256)
if not data:
    continue
now_ns = self.get_clock().now().nanoseconds
for reading in self._parser.feed(data, now_ns):
    self._handle_reading(reading)
```

`self._parser.feed()` frames sentences and strips inter-sentence padding
(`lstrip(b'\n')`, empty-sentence `continue` in `AMLParser.feed`/`RegexParser.feed`,
`sound_speed_bridge/sound_speed_bridge/parsers.py`). The existing `raw` topic
(added by #75/PR#76) publishes `SoundSpeedReading.raw_bytes` — the framed
sentence, terminator included — from `_handle_reading`. That is downstream of
framing, so two failure modes go unrecorded:

1. **Wrong baud / bus-voltage-sag corruption**: bytes never frame (the field
   evidence in the issue: LF reads as NUL once the bus sags, the CRLF regex
   framer never terminates a line). Zero readings -> zero `raw` messages. The
   topic is silent exactly when the operator most needs bag evidence.
2. Even framed traffic isn't reconstructed byte-exactly by concatenating
   `raw` messages, because padding is stripped before `raw_bytes` is built.

This plan adds a second publisher that taps the wire stream *before* framing,
in `_serial_loop`, so the bag captures exactly what arrived on the UART —
garbage included — and settles the three design points the issue and its
review (`## Issue Review` in this file's progress.md) leave open.

## Design Decisions

### 1. Complement the existing `raw` topic, don't replace it

Keep `raw` (per-sentence, post-framing, already an external contract per the
node test's comment referencing `unh_echoboats_project11#396`'s record list).
Add a new topic, `serial_tap`, publishing pre-framing byte chunks.

**Rationale**: `raw` is still useful for its own purpose — correlating a
specific parse failure with the exact sentence bytes that produced it,
already tied into `_parse_error_count`. `serial_tap` answers a different
question ("did the wire stream frame at all"), which `raw` cannot answer by
construction (it only exists once framing succeeds). Replacing `raw` would
regress the per-sentence correlation the existing tests
(`test_raw_publishes_on_parse_success`/`_failure`) depend on, for no gain.
Both topics stay useful for different RCA questions; #75 and
`echoboats#396`'s references to "the `raw` topic" continue to mean the
per-sentence topic unchanged — no update needed there since this plan does
not repoint or deprecate it.

### 2. Publish cadence: one message per non-empty `ser.read(256)` return

Tap directly at the existing read boundary — publish `data` as-is,
immediately after `ser.read(256)` returns and before it reaches
`self._parser.feed()`. No additional buffering, batching, or re-chunking.

**Rationale** (bag-volume / rate impact, per the "Only what's needed"
review flag):
- `ser.read(256)` is already bounded to ≤256 bytes per call — reusing it as
  the publish boundary adds no new unbounded-growth risk (unlike the
  parser's internal accumulation buffer that #78 addresses).
- At the sensor's actual data rates (9600–19200 baud typical for AML/Valeport
  probes, ~1–2 KB/s), `pyserial`'s `read(size)` with `timeout=1.0` returns
  early once 256 bytes have arrived (roughly every 130–266 ms at those
  rates), giving a tap rate of ~4–8 Hz under continuous data — well below
  the existing `raw` topic's plausible per-sentence rate for a >1 Hz sensor,
  and a small, predictable addition to bag volume (≤256 B × the read rate).
  Under a wrong-baud/no-data condition, `read()` returns `b''` at the 1 s
  timeout, which is already filtered out by `if not data: continue` — a
  silent link publishes nothing, exactly as a tap should (silence stays
  observable via `_serial_connected`/staleness diagnostics, not by tap
  volume).
- No re-chunking to fixed time/size batches: it would require a second
  buffer and its own flush/shutdown logic, i.e. reintroducing the exact
  class of complexity #78 is scoped to fix for the *parser's* buffer. The
  natural `read()` boundary is simpler, already exists, and is sufficient
  for the diagnostic question ("did anything arrive, and what was it,
  byte-exactly") without needing time alignment across messages.
- Concatenating `serial_tap` messages in arrival order reconstructs the wire
  stream byte-exactly, since a single reader thread publishes them in the
  order `read()` returned them and no bytes are dropped, reordered, or
  transformed between `ser.read()` and `publish()`.

### 3. Ordering vs. #78 (parser buffer cap)

No functional dependency either direction (confirmed in `## Issue Review`):
`serial_tap` reads from `ser.read()`, upstream of `SoundSpeedParser.feed()`'s
internal accumulation buffer that #78 caps. Whichever of #77/#78 lands first,
the other rebases cleanly — both touch the same `_serial_loop`/`parsers.py`
region, so the PR that lands second should note the other issue number in
its description (see Actions below); no code coordination is required.

### 4. Package README: not warranted by this issue

`sound_speed_bridge` has no README (pre-existing gap since PR#76, not
introduced here). This plan does not add one. The established pattern in
this file — the `raw` publisher's in-code doc comment directly above its
`create_publisher` call (`node.py:100-109`) — is proportionate for a single
new publisher and is what this plan follows for `serial_tap` too. A README
covering both raw-adjacent topics together (and the rest of the node's
parameters/behavior) is a reasonable follow-up but is a separable,
higher-scope task better done once, not fragmented across this issue and #78.
Flagged as a documentation candidate below, decision left to the operator.

## Approach

1. **Add the `serial_tap` publisher** in `SoundSpeedBridgeNode.__init__`,
   alongside the existing `raw` publisher (`node.py:110`), same `UInt8MultiArray`
   type and `RELIABLE` QoS (`topic_qos`), bare relative name `serial_tap`. Doc
   comment modeled on the `raw` publisher's, explaining this one taps
   pre-framing and is byte-exact including unframeable garbage.
2. **Publish from `_serial_loop`**, immediately after `ser.read(256)` returns
   non-empty data and before the parser sees it:
   ```python
   data = ser.read(256)
   if not data:
       continue
   if not self._stop_event.is_set():
       self._tap_pub.publish(UInt8MultiArray(data=data))
       self._tap_byte_count += len(data)
   now_ns = self.get_clock().now().nanoseconds
   for reading in self._parser.feed(data, now_ns):
       self._handle_reading(reading)
   ```
   The `_stop_event` check is a shutdown guard specific to this call site
   (same rationale as `_handle_reading`'s existing guard, `node.py:198-199`):
   `ser.read()` can block up to 1 s past `destroy_node()`'s 2 s best-effort
   join deadline, after which publishers may already be destroyed.
3. **Add a diagnostics counter**: `self._tap_byte_count` (total bytes
   tapped since node start), reset never (monotonic counter, matches the
   existing style of `_parse_error_count`/`_serial_reconnect_count`, which
   are also never reset). Surface it as a new `KeyValue` in
   `_publish_diagnostics` (`node.py:299-312`) — `tap_byte_count` — so an
   operator can confirm the tap is alive without inspecting bag content, per
   the issue review's consequence note.
4. **Tests** in `sound_speed_bridge/test/test_node.py`:
   - `test_serial_tap_publishes_raw_chunk`: drive `_serial_loop` with a
     mocked `ser.read` `side_effect` returning one chunk then `b''`
     (stop_event set from a side effect or a bounded iteration count so the
     loop exits deterministically); assert `serial_tap` receives exactly
     that chunk's bytes, unmodified.
   - `test_serial_tap_captures_unframeable_garbage`: feed bytes with no
     terminator at all (e.g. `b'\xff\xfe\x00\x00'`, matching the field
     evidence in the issue) through `_serial_loop`; assert `serial_tap`
     publishes them even though `self._parser.feed()` yields no readings
     and `raw` never publishes.
   - `test_serial_tap_concatenation_reconstructs_stream`: drive multiple
     `ser.read()` chunks (varied sizes, including a split mid-sentence
     terminator) and assert concatenating the `serial_tap` messages in
     publish order equals the concatenation of the input chunks — i.e. no
     byte loss, duplication, or reordering across chunk boundaries.
   - `test_serial_tap_noop_after_stop`: set `_stop_event` before the tap
     publish call executes (patch `ser.read` to return data, set the event
     inside a `side_effect` before returning) and assert `serial_tap`
     receives no publish — exercising the new shutdown guard from step 2.
   - Extend the existing topic-identity assertion pattern in `_make_node`
     (`node.py` test file, currently asserts `raw`'s bare name/type) to also
     assert `node._tap_pub.topic_name == '/serial_tap'` and
     `msg_type is UInt8MultiArray`, so a future rename is caught the same
     way the `raw` contract is.
   These directly satisfy the issue review's "Test what breaks" actions
   (unframeable-garbage capture, no loss/duplication/reordering, shutdown-race
   guard on the new publish path).
5. **Cross-link #77 and #78** in the PR description (`Part of` / See-also,
   not a closing keyword — both remain open, independent issues per
   AGENTS.md's issue-closing-keyword rule), noting both touch
   `_serial_loop`/`parsers.py`'s shared pre-framing region.

## Files to Change

| File | Change |
|------|--------|
| `sound_speed_bridge/sound_speed_bridge/node.py` | Add `_tap_pub` (`serial_tap`, `UInt8MultiArray`, `RELIABLE`) in `__init__`; publish from `_serial_loop` pre-framing with shutdown guard; add `_tap_byte_count` counter and diagnostics `KeyValue` |
| `sound_speed_bridge/test/test_node.py` | Add tap tests (chunk passthrough, unframeable-garbage capture, concatenation/no-loss, shutdown guard); extend topic-identity assertions in `_make_node` |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Human control and transparency | New topic is observable/bag-recordable; no change to existing `sound_speed`/`raw`/diagnostics topic behavior or schema |
| Capture decisions, not just implementations | Replace-vs-complement, cadence, and #78 ordering are recorded above with rationale, not defaulted |
| Only what's needed | Tap reuses the existing bounded `read(256)` boundary — no new buffer, no re-chunking scheme; rate/volume impact is estimated and bounded (see Design Decision 2) |
| A change includes its consequences | Diagnostics counter added so the tap's aliveness is visible without bag inspection; README decided (not needed now) rather than left silently unaddressed; #78 cross-link keeps the shared-region change visible to whichever PR lands second |
| Test what breaks | Tests target exactly the field failure mode: unframeable garbage, byte-exact reconstruction across chunk boundaries, and the shutdown race already guarded for `_handle_reading` |
| Workspace vs. project separation | Project-specific sensor driver work, scoped to `marine_tools` |
| Improve incrementally | Builds on #75/PR#76's `raw` topic and conventions without reworking them |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| ADR-0008 (ROS 2 conventions) | Yes | New publisher matches the existing `raw` topic's established conventions in this file: bare relative topic name, `RELIABLE` QoS via the shared `topic_qos`, `UInt8MultiArray` message type. No divergence, so no deviation ADR needed |
| ADR-0013 (progress.md vocabulary) | Yes (process) | This plan and its progress.md entry follow the vocabulary |
| Others | No | Not triggered |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| Add `serial_tap` publisher/topic | Node's startup log line (`node.py:131-134`), which currently doesn't enumerate topics — no change needed, topics are self-describing via `ros2 topic list` | N/A — no action needed |
| Add `serial_tap` publisher/topic | `sound_speed_bridge` README | Deliberate no (Design Decision 4) — flagged as a documentation candidate below |
| Add a diagnostics counter | `_publish_diagnostics`'s `KeyValue` list, existing pattern | Yes — included in Approach step 3 |
| Field-diagnosis chain docs (#75, `echoboats#396`) referencing "the `raw` topic" | No update needed — `raw`'s meaning and topic name are unchanged; `serial_tap` is additive | Yes — decided in Design Decision 1, no follow-up needed |

## Documentation & Instruction Impact

- **Stale docs** (must land in this PR): None — no README exists for
  `sound_speed_bridge` to go stale; the new publisher gets its own in-code
  doc comment (Approach step 1), matching the existing `raw` publisher's
  pattern in the same file, so no separate doc file is invalidated by this
  change.
- **Agent-instruction candidates** (proposals only — operator decides): A
  `sound_speed_bridge` package README covering both raw-adjacent topics
  (`raw` and `serial_tap`), the node's parameters, and the two-topic RCA
  story (per-sentence correlation vs. byte-exact wire capture) would be
  useful now that the gap has widened to two related-but-distinct topics.
  Not proposed as part of this PR (Design Decision 4) — worth its own issue
  if the operator wants it tracked.

## Open Questions

- None — the three design points the issue and its review flagged are
  settled above (Design Decisions 1-3); README timing is also settled
  (Design Decision 4, flagged as a future candidate, not a blocker).

## Estimated Scope

Single PR.
