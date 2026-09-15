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
   evidence in the issue: LF reads as NUL once the bus sags, so the CRLF
   regex framer never terminates a line). Zero readings -> zero `raw`
   messages. The topic is silent exactly when the operator most needs bag
   evidence.

   *Which framer this is* (review suggestion 7 — the reviewer read the
   package default, which is not what the field runs): the package default
   `parser: aml` frames on a single `\r` (`parsers.py:75`), but the
   BizzyBoat deployment launch that produced the field evidence
   (`unh_echoboats_project11`'s `sound_speed_launch.py`) sets
   `parser: regex` with `regex_line_terminator: 'crlf'` and
   `regex_pattern: '\$AML,SVM,(?P<sound_speed>\d+\.\d+)'`, i.e. the
   two-byte `\r\n` terminator of `RegexParser` (`parsers.py:138,166`).
   So the "CRLF framer" wording above is correct **for the field case**,
   and is deliberately kept. The distinction matters for how the failure
   presents: with CRLF framing, a single corrupted `\n` (the observed
   `\r\x00` in the field bytes) loses the terminator entirely and the
   stream never frames; with the `aml` default's single `\r`, framing
   survives that particular corruption and the damage shows up as parse
   failures instead. The conclusion — `raw` cannot record a stream that
   never frames — holds for both, but only the CRLF (field) case produces
   the total silence the issue reports.
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
- **Rate is set by the probe's sentence rate, not by the link's baud**
  (review finding 5 — the first draft of this plan derived it from baud,
  i.e. link *capacity*, which overestimates by ~4x whenever the probe is
  the bottleneck, as it always is here). The field probe emits ~25
  sentences/s of ~11–32 B each (`1500.123\r\r\n` for the `aml` form;
  `$AML,SVM,1515.217,SN,200937*05\r\n` for the field regex form), i.e.
  ~275–800 B/s on the wire. `pyserial`'s `read(256)` with `timeout=1.0`
  returns as soon as 256 B have accumulated, so it returns roughly **once
  per second** → a tap rate of ~1 Hz, ~32k messages and ~8 MB of payload
  over an 8 h recording. The hard bound is the link, not the probe:
  baud/10 = 960 B/s at the default 9600 baud → ≤27.6 MB per 8 h even if
  something saturates the UART. Both numbers are acceptable bag volume for
  a deployment recording, and the tap adds ~1 msg/s against the ~25 msg/s
  the `raw` topic already publishes at the same sentence rate.
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
  stream byte-exactly **within one serial connection**, since a single
  reader thread publishes them in the order `read()` returned them and no
  bytes are dropped, reordered, or transformed between `ser.read()` and
  `publish()`.

  **Qualification across reconnects** (review finding 3): on a
  `SerialException`/`OSError` the loop closes the port, waits
  `reconnect_delay_sec`, and reopens (`node.py:184-190`). Bytes in flight
  during that window are lost in the driver, and the tap emits **no in-band
  marker** for the discontinuity — so a naive concatenation of the whole
  bag silently splices two connections together. This is not hypothetical:
  the bus-voltage-sag condition in the field evidence is exactly what
  produces those exceptions. The cross-check is the existing
  `serial_reconnect_count` KeyValue in `/diagnostics`: byte-exactness of a
  concatenated stream may only be claimed over a span in which that counter
  did not change. No in-band marker message is added — it would put a
  synthetic, non-wire byte sequence into a topic whose entire contract is
  "these bytes came off the UART", and the reconnect is already both logged
  (ERROR) and counted. Recorded here so the limitation is in the record
  rather than discovered during an RCA.

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

   **Time base, recorded as a decision** (review suggestion 8):
   `UInt8MultiArray` has no header, so the bag's receive time is the only
   timestamp a tap message carries — and post-hoc temporal correlation
   ("when did the bytes stop framing relative to the power event") is the
   tap's whole purpose. Accepted anyway: it matches the existing `raw`
   topic (same type, same absence), bag receive time is within one
   `read()` interval (~1 s) of the wire arrival, and that is the resolution
   the diagnostic question needs. The alternative — a stamped custom
   message — would introduce a new message type into an external bag
   contract (`echoboats#396`'s record list) for sub-second precision that
   no one is asking for.
2. **Publish from `_serial_loop`**, tapping the bytes `ser.read(256)`
   returned — but **after** the parser feed loop, and inside its own
   `try/except`:
   ```python
   data = ser.read(256)
   if not data:
       continue
   now_ns = self.get_clock().now().nanoseconds
   for reading in self._parser.feed(data, now_ns):
       self._handle_reading(reading)
   self._publish_serial_tap(data)
   ```
   with the tap publish itself isolated in a small helper:
   ```python
   def _publish_serial_tap(self, data: bytes) -> None:
       if self._stop_event.is_set():
           return
       try:
           self._tap_pub.publish(UInt8MultiArray(data=data))
       except Exception as exc:
           self._tap_error_count += 1
           self.get_logger().warning(
               f'serial_tap publish failed: {exc}', throttle_duration_sec=10.0)
           return
       self._tap_byte_count += len(data)
   ```

   **Both mitigations, and why** (review finding 1 asked for one; the file's
   own rule and the failure mode argue for both, and they answer different
   halves of the risk):
   - *Ordering* — publishing after the feed loop follows the explicit rule
     this file already states at `node.py:219-221` for the `raw` publisher:
     a diagnostic-only publish never precedes the primary `SoundSpeed`
     path, so a slow or failing diagnostic cannot delay or preempt the
     readings the vehicle actually consumes.
   - *Isolation* — ordering alone is not sufficient, because
     `_serial_loop` catches only `(SerialException, OSError)`. Any other
     exception escaping the tap publish propagates out of `_serial_loop`,
     **ends the reader thread permanently** (no reconnect, no restart, the
     node keeps running and publishing stale diagnostics), and does so in
     precisely the degraded condition the tap was added to observe. A
     diagnostic must not be able to kill the sensor. The `except Exception`
     is therefore deliberate and broad — narrower would leave the failure
     mode open — and it is not silent: it counts (`tap_error_count`) and
     logs throttled, so the swallowed error is visible in `/diagnostics`
     rather than hidden (the "no silent failures" clause of the Quality
     Standard).
   - *Consequence of the ordering choice, recorded*: within one chunk the
     `raw` messages for that chunk's sentences are published **before** the
     `serial_tap` message containing those same bytes. Tap-to-tap order is
     unaffected (single reader thread), so byte-exact reconstruction from
     `serial_tap` alone is unchanged; only cross-topic interleaving in the
     bag shifts, by well under one `read()` interval.

   The `_stop_event` check is a shutdown guard specific to this call site
   (same rationale as `_handle_reading`'s existing guard, `node.py:198-199`):
   `ser.read()` can block up to 1 s past `destroy_node()`'s 2 s best-effort
   join deadline, after which publishers may already be destroyed.
3. **Add diagnostics counters**: `self._tap_byte_count` (total bytes
   tapped since node start) and `self._tap_error_count` (tap publishes that
   raised, per step 2), both monotonic and never reset — matching the
   existing style of `_parse_error_count`/`_serial_reconnect_count`.
   Surface both as new `KeyValue`s in `_publish_diagnostics`
   (`node.py:299-312`) — `tap_byte_count`, `tap_error_count` — so an
   operator can confirm the tap is alive, and see when it is not, without
   inspecting bag content. Like the existing counters these are written
   from the serial thread and read from the diagnostics timer without the
   lock; that is the established pattern in this file and int increments
   under CPython's GIL cannot tear a counter into an invalid value, so the
   worst case is a reading one chunk stale.

   **Consequence — `/diagnostics` must be recorded** (review finding 4):
   the tap distinguishes "the probe is corrupt" from "the probe is silent"
   by *presence* of bytes; but distinguishing "the probe is silent" from
   "the node never ran / the topic was not recorded" rests entirely on
   `tap_byte_count` being visible, i.e. on `/diagnostics` being in the
   deployment bag record list — the same external contract
   (`rolker/unh_echoboats_project11#396`) that the `raw` topic's test
   already cites for `raw`. Absence of `serial_tap` messages is only
   evidence of a silent probe when `/diagnostics` is in the bag alongside
   it. Recorded in the Consequences table below as a cross-repo follow-up
   to verify, not a code change in this PR.
4. **Test harness first** (review finding 6): every tap test must drive
   `_serial_loop`, but the shared `_make_node` helper deliberately stops the
   serial thread immediately after construction, and every existing test
   calls `_handle_reading` directly. `_make_node`'s rationale must be
   preserved as-is — its mocked `read` returns `b''` instantly, so a live
   thread would busy-spin at 100% CPU for the node's lifetime; that is why
   it is killed, not an accident. So add a second helper alongside it,
   `_drive_serial_loop(node, mock_serial_cls, chunks, stop_before_index=None)`,
   which:
   - installs a fake serial port whose `read` side-effect pops the next
     chunk from `chunks`, and on exhaustion sets `_stop_event` and returns
     `b''` (so the `if not data: continue` re-tests the `while` condition
     and the loop exits deterministically — no timing, no sleeps, no live
     thread);
   - clears `_stop_event` and calls `node._serial_loop()` **synchronously on
     the test thread**, so assertions run after the loop has provably
     finished;
   - optionally sets `_stop_event` *before* returning a given chunk
     (`stop_before_index`), which is how the shutdown-guard test reaches the
     new publish path with the event already set;
   - asserts on exit that every chunk was consumed, so a loop that bailed
     early cannot pass a test by publishing nothing.

   The real parser is left in place (not mocked) so the "garbage yields no
   readings" assertions exercise the actual framing code.

5. **Tests** in `sound_speed_bridge/test/test_node.py`:
   - `test_serial_tap_publishes_read_chunk`: drive `_serial_loop` with one
     chunk; assert `serial_tap` receives exactly that chunk's bytes,
     unmodified.
   - `test_serial_tap_captures_unframeable_field_garbage` and
     `test_serial_tap_captures_all_nul_chunk` (split in implementation: two
     distinct field signatures, so a failure names which one): drive the loop
     with the **field bytes** from the issue —
     `b'$AML,SVM,1515.217,SN,200937*05\r\x00$AML,SVM,1515.180,SN,20 937*08\r\x00'`
     (the observed `\r\x00` corruption of `\r\n`) against a `regex`
     parser configured as the field launch configures it
     (`crlf` terminator, the `$AML,SVM,...` pattern; installed by assigning
     a `RegexParser` onto the constructed node rather than by re-initialising
     the rclpy context with parameter overrides — the loop under test reads
     `self._parser`, so this exercises the field framing directly and keeps
     the fixture simple) — and, separately, an all-NUL chunk. Assert in both cases that `serial_tap` publishes the
     bytes verbatim while the parser yields **no** readings and `raw`
     publishes nothing: the exact silent-`raw` condition the issue reports.
   - `test_serial_tap_concatenation_reconstructs_stream`: drive multiple
     `ser.read()` chunks (varied sizes, including a split mid-sentence
     terminator) and assert concatenating the `serial_tap` messages in
     publish order equals the concatenation of the input chunks — i.e. no
     byte loss, duplication, or reordering across chunk boundaries.
   - `test_serial_tap_noop_after_stop`: set `_stop_event` before the tap
     publish call executes (`stop_before_index=0`) and assert `serial_tap`
     receives no publish — exercising the new shutdown guard from step 2.
   - `test_serial_tap_publish_failure_is_counted_not_fatal` (added during
     implementation, covering the second half of finding 1): make the tap
     publish raise and assert the loop still consumes every chunk, the
     primary `sound_speed` path still publishes every reading,
     `tap_error_count` counts each failure, and `tap_byte_count` stays 0
     because nothing reached the wire. Without the `try/except` this test
     fails, as does the shutdown-guard test without its guard — both were
     verified by removing the code under test.
   - `test_tap_counters_surface_in_diagnostics`: after driving the loop
     with known chunks, assert `_tap_byte_count` equals the total input
     length and that `_publish_diagnostics` emits a `tap_byte_count`
     (and `tap_error_count`) `KeyValue` carrying it — the operator-visible
     half of the silent-vs-absent discrimination.
   - Extend the existing topic-identity assertion pattern in `_make_node`
     (`node.py` test file, currently asserts `raw`'s bare name/type) to also
     assert `node._tap_pub.topic_name == '/serial_tap'` and
     `msg_type is UInt8MultiArray`, so a future rename is caught the same
     way the `raw` contract is.
   These directly satisfy the issue review's "Test what breaks" actions
   (unframeable-garbage capture, no loss/duplication/reordering, shutdown-race
   guard on the new publish path).
6. **Cross-link #77 and #78** in the PR description (`Part of` / See-also,
   not a closing keyword — both remain open, independent issues per
   AGENTS.md's issue-closing-keyword rule), noting both touch
   `_serial_loop`/`parsers.py`'s shared pre-framing region.

## Files to Change

| File | Change |
|------|--------|
| `sound_speed_bridge/sound_speed_bridge/node.py` | Add `_tap_pub` (`serial_tap`, `UInt8MultiArray`, `RELIABLE`) in `__init__`; add `_publish_serial_tap()` (shutdown guard + own `try/except`) called from `_serial_loop` after the feed loop; add `_tap_byte_count`/`_tap_error_count` counters and their diagnostics `KeyValue`s; repoint the stale "see #77 for a true byte-stream tap" comment at `node.py:107` |
| `sound_speed_bridge/test/test_node.py` | Add the `_drive_serial_loop` harness; add tap tests (chunk passthrough, field-bytes + all-NUL unframeable capture, concatenation/no-loss, shutdown guard, diagnostics counters); extend topic-identity assertions in `_make_node`; repoint the stale module-docstring reference to #77 at `test_node.py:9` |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Human control and transparency | New topic is observable/bag-recordable; no change to existing `sound_speed`/`raw`/diagnostics topic behavior or schema |
| Capture decisions, not just implementations | Replace-vs-complement, cadence, and #78 ordering are recorded above with rationale, not defaulted |
| Only what's needed | Tap reuses the existing bounded `read(256)` boundary — no new buffer, no re-chunking scheme; rate/volume impact is derived from the probe's sentence rate (~1 Hz tap, ~8 MB/8 h) with the baud/10 hard bound as the ceiling (see Design Decision 2) |
| A change includes its consequences | Stale in-code references repointed; `/diagnostics` record-list dependency recorded; reconnect discontinuity qualified; diagnostics counters added so the tap's aliveness is visible without bag inspection; README decided (not needed now) rather than left silently unaddressed; #78 cross-link keeps the shared-region change visible to whichever PR lands second |
| Test what breaks | Tests target exactly the field failure mode: unframeable garbage, byte-exact reconstruction across chunk boundaries, and the shutdown race already guarded for `_handle_reading` |
| Workspace vs. project separation | Project-specific sensor driver work, scoped to `marine_tools` |
| Improve incrementally | Builds on #75/PR#76's `raw` topic and conventions without reworking them; the tap publishes *after* the feed loop so the existing primary path keeps its priority and ordering (Approach step 2) |

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
| Add `serial_tap` + `tap_byte_count` as the silent-vs-absent discriminator | `rolker/unh_echoboats_project11#396`'s deployment bag record list must carry **`/diagnostics`** (and `serial_tap`) — absence of tap messages only means "probe silent" if `tap_byte_count` is in the bag to prove the node was running | Recorded, cross-repo — no code change here; verify against the record list when this lands (Approach step 3) |
| In-code references describing the byte-stream tap as future work (`node.py:107`, `test/test_node.py:9`) | Repoint both at `serial_tap` | Yes — Documentation & Instruction Impact |
| Tap publish can raise | `tap_error_count` counter + throttled WARN, so the broad `except` is never silent | Yes — Approach steps 2 and 3 |

## Documentation & Instruction Impact

- **Stale docs** (must land in this PR) — the first draft said "None",
  which was wrong (review finding 2). No README exists for
  `sound_speed_bridge`, but two in-code references describe this tap as
  *future work* and become false the moment it lands. Both are in files
  this PR already edits, so both are repointed here:
  1. `sound_speed_bridge/sound_speed_bridge/node.py:107` — the `raw`
     publisher's doc comment ends "See rolker/marine_tools#77 for a true
     byte-stream tap." Repoint to name the `serial_tap` topic as the
     existing companion, keeping the description of what `raw` does and
     does not capture (that part is still accurate and still useful).
  2. `sound_speed_bridge/test/test_node.py:9` — the module docstring says a
     never-framing stream "publishes nothing (see rolker/marine_tools#77)".
     Repoint to `serial_tap`, and widen the docstring: the module is no
     longer only about the `raw` passthrough.

  The new publisher also gets its own in-code doc comment (Approach step
  1), matching the existing `raw` publisher's pattern.

  Not stale, deliberately: this repo has no `.agents/README.md` (the
  top-level `AGENTS.md` says so explicitly), so there is no verified
  topic/parameter table to update for the new topic.
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

## Revision History

- **rev 2** (this revision, pre-implementation): resolves all six
  `## Plan Review` findings — publish placement + exception isolation
  (finding 1, Approach step 2); stale in-code references repointed
  (finding 2, Documentation & Instruction Impact + Files to Change);
  reconstruction claim qualified across reconnects with
  `serial_reconnect_count` as the cross-check (finding 3, Design Decision
  2); `/diagnostics` record-list dependency recorded as a consequence
  (finding 4, Approach step 3 + Consequences); volume restated from the
  probe's sentence rate (finding 5, Design Decision 2); test harness for
  driving `_serial_loop` named as its own step (finding 6, Approach step
  4). Also records both suggestions (7: the framer attribution — see
  Context; the reviewer's correction was right about the package default
  and wrong for the field deployment, so the CRLF wording stands with the
  distinction recorded; 8: `UInt8MultiArray` has no header, so bag receive
  time is the tap's only time base — Approach step 1).

## Estimated Scope

Single PR.
