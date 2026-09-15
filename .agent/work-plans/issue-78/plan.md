# Plan: parsers: unbounded accumulation buffer — cap + trim WARN

## Issue

https://github.com/rolker/marine_tools/issues/78

> Revision 2 (2026-09-15) resolves all 5 must-fix and 7 should-fix findings
> of the Plan Review entry in `progress.md`. Each resolution is marked
> **[PR-F*n*]** against the finding it answers, with the rationale inline.

## Context

`AMLParser.feed()` and `RegexParser.feed()` in
`sound_speed_bridge/sound_speed_bridge/parsers.py` accumulate incoming
serial bytes into `self._buffer` with no cap. If the configured line
terminator never matches, the buffer grows without bound for as long as
bytes arrive, and if the terminator later matches once, the entire
accumulated buffer is emitted as a single multi-MB "sentence" whose
`raw_bytes` lands on the RELIABLE `raw` topic (`node.py:222`), now
always-recorded in BizzyBoat's main deployment bag
(rolker/unh_echoboats_project11#396).

### Which failure actually stalls which parser **[PR-F3]**

Plan rev 1 stated that the field-observed AML UART collapse (LF read as
NUL, 17 BizzyBoat deployment bags) stalls the accumulation buffer. That is
**wrong**, and the code it cited contradicts it:

- `AMLParser._TERMINATOR` is a bare `\r` (`parsers.py:75,89`). The AML SVS
  emits CRCRLF, but the parser frames on the **first CR** and treats the
  trailing `\n` as padding. LF→NUL therefore leaves the `\r` intact, every
  sentence still frames, and the AML buffer never grows. What LF→NUL
  actually does is leave a NUL at the head of the *next* sentence —
  `lstrip(b'\n')` (`parsers.py:88`) does not strip NUL, so
  `Decimal('\x001500.0')` raises `InvalidOperation` and every subsequent
  reading is NaN. **That defect is rolker/marine_tools#90 and is
  deliberately out of scope here** — #78's cap does nothing for it, and
  #78 must not be recorded as the fix for the field failure. **[PR-F4]**

The stalls this issue's cap *does* bound are:

1. A **misconfigured `regex_line_terminator`** — `regex` framing is exact
   (`cr` | `lf` | `crlf`, `parsers.py:138`), so a sensor emitting LF against
   a `crlf` configuration never frames at all, for the whole run.
2. **Corruption of the framing byte itself** — for `aml`, the `\r`; for
   `regex`, the configured terminator (and under `crlf`, either half).
   Same UART-collapse family as the field event, different byte.

Both are open-ended: they persist until the config is fixed or the wiring
is, i.e. the 1–5 h scale the field bags show for the related NUL fault.

### Sizing basis, re-derived **[PR-F3]**

Wire rate is bounded: ~25 Hz × ~32 B ≈ 800 B/s observed (the issue's
~1 KB/s at 9600 baud is the same order). Sizing is therefore driven by the
read chunk and the sentence length, not by the stall duration — a stall of
*any* length is bounded to the cap, so the cap only has to be (a) larger
than anything legitimate and (b) small enough that the retained glued line
is a diagnostic sample rather than a memory hazard.

## Approach

### 1. Shared cap/trim logic in the `SoundSpeedParser` ABC

`self._buffer` initialization moves **into the ABC** alongside the helpers
that mutate it **[PR-F12a]**, together with the cap, the counters, and the
resync flag. Both concrete parsers call `super().__init__(max_buffer_bytes)`.
Centralizing avoids duplicating the logic across two parsers
(*Only what's needed*).

ABC state:

| Attribute | Meaning |
|---|---|
| `_buffer` | unframed bytes (moved up from the concrete classes) |
| `_max_buffer_bytes` | cap on **unframed residue** (see step 2) |
| `_discarding` | True when the residue was trimmed and the remainder of that sentence must be thrown away |
| `buffer_dropped_bytes` | total bytes discarded by trims (public, polled) |
| `buffer_trim_count` | number of trim events (public, polled) |

`AMLParser` gains `self._terminator = self._TERMINATOR` so the shared
helpers have one attribute to frame on for both parsers.

### 2. Trim the **residue at the end of `feed()`**, never at append **[PR-F2]**

Plan rev 1 trimmed on append, *before* the framing loop ran. That drops
complete, framable sentences whenever a read chunk is larger than the cap
(`ser.read(256)`, `node.py:178`) — silent data loss on a +1 counter.

Revised order inside `feed()`:

1. append `data` to `_buffer`;
2. if `_discarding`, resync (step 3) — and if no terminator has arrived
   yet, trim and return no readings;
3. run the existing framing loop, collecting readings;
4. **then** trim whatever unframed residue is left.

The cap therefore means exactly "**maximum unframed residue**" and is
independent of the read chunk size: a 64 KiB read full of complete
sentences frames every one of them and trims nothing, at any cap.

**`feed()` becomes eager (returns a list) rather than a generator.**
Both `feed()` implementations are generators today, so the buffer is only
mutated while the caller iterates — a `feed()` whose result is dropped
silently discards the data, and "the cap is enforced on every feed" would
depend on the caller exhausting the iterator. Buffer management must not
be contingent on consumer behaviour, so both `feed()`s build and return a
list. The declared return type stays `Iterable[SoundSpeedReading]`; the
node's `for reading in self._parser.feed(...)` and the tests' `list(...)`
are unaffected. Chunk yields are a handful of readings, so eagerness costs
nothing.

### 3. After a trim, discard through the next terminator **[PR-F1]**

Drop-oldest leaves a mid-sentence fragment at the front of the buffer. If
the next terminator simply frames it, the fragment is published as a whole
sentence — and for `RegexParser` this is worse than NaN: `_parse` uses
`re.search` (`parsers.py:189`), so `...1497.3` sliced mid-number can match
a **plausible but wrong** sound speed and publish it on the RELIABLE
`sound_speed` topic. A wrong-but-credible sound speed propagates into
sonar refraction and into the CUBE store; a NaN does not.

Rule: **a trim marks the residue suspect (`_discarding = True`); the
parser yields nothing until it has consumed bytes through the next
terminator**, then frames normally from there. Because trimming happens
only after framing, the residue holds no terminator, so "discard through
the next terminator" discards exactly the remainder of the one broken
sentence — never a complete one.

Drop-oldest (keep the last `_max_buffer_bytes` bytes) is still the right
trim, rather than clearing the buffer outright: the retained tail is what
lets a terminator **straddling the trim** be recognised (a kept `\r` whose
`\n` arrives in the next chunk under `crlf`), which a wholesale clear would
miss — costing one extra discarded sentence per trim.

### 4. Cap default 4096 bytes, floor 256 bytes

- **Default 4096 (4 KiB)**: 16× the 256 B serial read chunk, 16× the
  longest legitimate configured regex line (~256 B; AML sentences are
  under 32 B), and ~5 s of wire at 800 B/s. It bounds the multi-MB hazard
  by roughly three orders of magnitude.
- **Floor 256, `ValueError` below it**: 256 B is simultaneously the serial
  read size (`node.py:178`) and the longest legitimate sentence. Below
  that floor, every single read chunk would overflow the cap even in
  healthy traffic, and a legitimate long sentence could never frame at
  all. Above it, trimming can only ever be triggered by unframed residue,
  i.e. by a genuine framing stall. Validated identically in the ABC and at
  the node parameter declaration.

**Honest statement of what the cap loses** **[PR-F6]**: rev 1 claimed the
cap "never truncates a real sentence". Not true of the case the cap exists
for. During a stall the unit that eventually frames *is* the glued
multi-sentence line, and the cap bounds it to 4 KiB — its **head is lost**,
and the trimmed remainder is then discarded through the next terminator
(step 3) rather than published as a fragment. What survives is the
counters, the WARN, and the healthy sentences after resync. **The
byte-exact recovery path for the dropped bytes is `serial_tap` from
rolker/marine_tools#77 (PR #89)**, which taps the wire upstream of the
parser; #78 deliberately does not try to preserve the bytes itself.

**Secondary benefit** **[PR-F12b]**: the cap also bounds the *pre-existing*
per-chunk O(n) framing cost — `lstrip` and the `self._buffer[idx + 1:]`
slice rebuild the whole buffer on every framing iteration, which is O(stall
length) today and becomes O(cap) once bounded. Trimming itself is one
O(cap) = 4 KiB slice per chunk at 25 Hz: negligible.

### 5. Node parameter

`declare_parameter('parser_max_buffer_bytes', 4096)` in
`SoundSpeedBridgeNode.__init__`, alongside the existing `regex_*`
parameters — static (read once at construction), matching every other
parser-tuning parameter in this node. Validated at the declaration site so
the error names the *parameter* (the parsers validate their constructor
argument independently, for direct library users). Both `PARSERS` factory
lambdas pass it through.

### 6. Counters: bytes **and** events **[PR-F7]**

Rev 1 counted trim events only. At 25 Hz a stall produces one trim per
chunk, so `buffer_trim_count` is really a proxy for elapsed stall time.
**Decision: publish both, and lead with bytes.**

- `buffer_dropped_bytes` is the actionable magnitude — how much of the
  stream was lost — and is what the WARN text quotes.
- `buffer_trim_count` is kept because it is the cheap **edge detector**
  the node's back-off needs ("has any new trim happened since the last
  tick?"), and because it distinguishes one large overflow from a
  sustained stall at the same byte total. Two ints; no further state.

Both are plain public attributes polled by `_publish_diagnostics`, matching
how it already polls `self._rate_hz` and `self._parse_error_count` rather
than being pushed updates. Cross-thread reads of a Python int are atomic
under the GIL — the same assumption the existing counters already make.

### 7. WARN: first trim immediately, then exponential back-off **[PR-F8]**

Rev 1's 1 Hz WARN is ~18k lines over a 5 h stall, while
`_publish_diagnostics` is already reporting ERROR (stale reading)
throughout that window. Rule, evaluated on the existing 1 Hz diagnostics
timer (no new timer):

- the **first** trim WARNs immediately (interval starts at 0 s);
- after each WARN the minimum interval doubles: 1, 2, 4, … s, **capped at
  300 s**;
- if a tick sees no new trims and the last WARN is older than the 300 s
  cap, the interval resets to 0 so a *later, separate* stall warns
  promptly again.

Volume over a 5 h stall: ~9 lines in the first ~9 minutes, then one per
5 minutes — ≈70 lines total instead of ~18,000. The WARN text names the
bytes dropped since the last WARN, the running total, and the trim count.

### 8. Diagnostics KeyValues

Add `buffer_dropped_bytes` and `buffer_trim_count` to
`_publish_diagnostics`'s `status.values`, after `parse_error_count` (the
other parser-health counter) and before `udp_send_error_count`. No new
topic — matches the operator's recorded preference that #78 add no bag
volume.

### 9. Docstrings

Update the module docstring (`parsers.py:1-10`) and the `SoundSpeedParser`
ABC docstring to describe the cap, the drop-oldest trim, the
discard-through-terminator resync, and the eager-`feed()` contract,
alongside the existing framing-quirk documentation.

**README**: `sound_speed_bridge` has no package README today. The
parameter row for `parser_max_buffer_bytes` is **deferred to
rolker/marine_tools#88**, which creates that README **[PR-F12c]**; #78
does not create a README solely to hold one row.

### 10. Tests

**File placement follows the existing split** **[PR-F9]**: `RegexParser`
tests go in `test/test_regex_parser.py`; `AMLParser` tests go in
`test/test_parsers.py` (AML-only today). Node tests in `test/test_node.py`.

Per parser (both files):

- **cap bounds the residue**: feed far more than the cap with no
  terminator; assert `len(parser._buffer) <= max_buffer_bytes`,
  `buffer_dropped_bytes > 0`, `buffer_trim_count > 0`.
- **drop-oldest keeps the tail**, and **no fragment is ever framed**
  **[PR-F5]**: rev 1's test fed garbage past the cap then "a well-formed
  sentence" and asserted a correct reading — wrong: the two glue into one
  line and yield NaN. Corrected spec: feed unframed garbage past the cap,
  **then a terminator** (which flushes the suspect residue and yields
  **nothing**), **then** a well-formed sentence — and assert *that*
  sentence frames correctly. Two assertions in one test: no fragment
  reading, and resync works.
- **resync to the next good sentence**: after a trim, a stream of several
  good sentences yields all of them except the first (the one whose head
  was lost).
- **CRLF straddle** **[PR-F10]**: a trim landing inside `\r\n` (and a
  retained `\r` whose `\n` arrives in the next chunk) resyncs on that
  terminator and does not swallow the following sentence. For `aml`, the
  analogous `\n`-padding boundary.
- **no spurious trim under normal traffic**: many complete sentences in a
  single chunk *larger than the cap* frame fully with
  `buffer_trim_count == 0` — the regression test for the trim-on-append
  defect (F2).
- **invalid cap rejected**: 0, negative, 1 and 255 (below the floor) each
  raise `ValueError` at construction, as does a non-integer cap (`4096.0`,
  `'4096'`, `None`, `True`) — a float or a string would otherwise compare
  or slice in ways that silently mis-size the buffer, and `True` is an
  `int` in Python, so the type check excludes `bool` explicitly.
- **exactly at the cap is not a trim**: a residue of precisely
  `max_buffer_bytes` leaves `buffer_trim_count == 0`, pinning the boundary
  of the comparison.

Node (`test_node.py`):

- `parser_max_buffer_bytes` below the floor raises `ValueError` at node
  construction, naming the parameter.
- `buffer_dropped_bytes` and `buffer_trim_count` KeyValues appear in
  `/diagnostics` and reflect the parser's counters.
- WARN fires on the first trim, is **not** repeated on the next tick
  (back-off), and fires again once the interval has elapsed.

## Branch sequencing (#77 / #78) **[PR-F11]**

**Decision: #89 (issue #77) merges first; #78 rebases onto `jazzy`
afterwards.** #78 develops and is reviewed independently on `jazzy` and is
*not* stacked on `feature/issue-77`.

Rationale and the conflict surface:

- The coupling is textual, not design: #77's `serial_tap` sits upstream of
  `parser.feed()` in `_serial_loop`, so it never touches the buffer cap.
- The overlap is **two files, not one**: both edit the
  `_publish_diagnostics` `KeyValue` list **and** `test_node.py` (plus node
  docstrings). Whoever merges second resolves both — additive list entries
  and additive test functions, no logic conflict.
- #89 goes first because it is the further-advanced PR (4 review rounds, a
  field check still open) and re-runs its full 62-test suite on any rebase;
  making it rebase onto #78 would spend that cycle again. #78 is younger
  and cheaper to rebase.
- Stacking #78 on an unmerged #89 would make #78 unreviewable and
  unmergeable on its own schedule.

## Out of scope

- **rolker/marine_tools#90** — AML NUL-prefix parse failure under the field
  LF→NUL UART collapse (`lstrip(b'\n')` does not strip NUL, so every
  subsequent sentence parses NaN). Filed separately; **not fixed here**
  **[PR-F4]**.
- **rolker/marine_tools#88** — package README, which will carry the
  `parser_max_buffer_bytes` parameter row **[PR-F12c]**.
- **rolker/marine_tools#77 / PR #89** — byte-exact wire tap, the recovery
  path for bytes this cap drops.

## Files to Change

| File | Change |
|------|--------|
| `sound_speed_bridge/sound_speed_bridge/parsers.py` | Move `_buffer` into the `SoundSpeedParser` ABC with `_max_buffer_bytes`, `_discarding`, `buffer_dropped_bytes`, `buffer_trim_count`; add `_resync()` + `_trim_residue()` helpers and floor validation; both `feed()`s become eager, trim residue at the end, and resync after a trim; `max_buffer_bytes` on both constructors; `PARSERS` factories pass it; module + ABC docstrings |
| `sound_speed_bridge/sound_speed_bridge/node.py` | `declare_parameter('parser_max_buffer_bytes', 4096)` + floor validation; `_last_buffer_trim_count`, `_last_buffer_dropped_bytes`, back-off state; backed-off WARN in `_publish_diagnostics`; two new `KeyValue`s |
| `sound_speed_bridge/test/test_parsers.py` | AML cap tests: bound, drop-oldest + no-fragment, resync, `\n`-padding boundary, no spurious trim on an oversize healthy chunk, invalid cap |
| `sound_speed_bridge/test/test_regex_parser.py` | Same set for `RegexParser`, plus the CRLF straddle and the `search`-matches-a-fragment case |
| `sound_speed_bridge/test/test_node.py` | Parameter validation; counters in `/diagnostics`; WARN once then backed off |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Test what breaks | Step 10 covers cap enforcement, the fragment hazard (the *reason* for the cap, and the one that can publish a wrong value), the trim-on-append data-loss regression, straddle boundaries, resync, and the WARN back-off. |
| A change includes its consequences | `/diagnostics` KeyValues and parser docstrings land in the same PR; the README row is explicitly routed to #88 rather than left implicit; #90 is filed rather than silently absorbed. |
| Human control and transparency | The parameter is declared, defaulted, floor-validated and justified; trims are visible as both bytes and events, and as a WARN that is loud once and then quiet. The plan states what the cap *loses*, not only what it bounds. |
| Only what's needed | Shared logic in the ABC, two ints of counter state, no new topic, no new timer, no per-parser trim strategy. |
| Improve incrementally | Two source files, three test files, no unrelated refactors; #90 and #88 stay separate. |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| ADR-0008 — ROS 2 conventions | Yes (lightly) | `parser_max_buffer_bytes` follows the existing `declare_parameter` + construction-time `ValueError` pattern already used for `regex_line_terminator`. |
| ADR-0013 — progress.md vocabulary | Yes | This plan and its progress entries follow the vocabulary. |
| Others | No | No tooling, packaging, deployment-mode or CI-verification changes. |

## Consequences

| If we change... | Also update... | Included? |
|---|---|---|
| `SoundSpeedParser` constructor contract | Both concrete `__init__`s, `PARSERS` factories | Yes — steps 1, 5 |
| `feed()` from generator to eager list | Node loop and tests (both already consume as an iterable) | Yes — step 2; no caller change needed |
| `_publish_diagnostics` `KeyValue` list | PR #89, which edits the same list and `test_node.py` | Yes — Branch sequencing; textual, resolved by whoever merges second |
| `parsers.py` docstrings | Cap/resync behaviour documented beside the framing quirks | Yes — step 9 |
| A new node parameter | Package README parameter table | Deferred to #88 (no README exists yet) — step 9 |
| A new node parameter | `launch/aml_svs.launch.py` | No change needed — the example launch sets only `device`/`baud`/`parser`/`frame_id` and leaves everything else at the node defaults, which now include the 4096-byte cap |

## Documentation & Instruction Impact

- **Stale docs** (land in this PR): `parsers.py` module + ABC docstrings,
  which describe buffering with no cap.
- **Agent-instruction candidates** (proposals only): none yet. The
  "back off after the first WARN rather than repeating on an existing
  periodic timer" pattern is a candidate for
  `.agent/knowledge/ros2_development_patterns.md` if it recurs; one
  instance does not warrant promoting it.

## Open Questions

- None. All 5 must-fix and 7 should-fix findings of the Plan Review are
  resolved above; the operator's checkpoint decision was "revise plan,
  then implement" with no further plan-review round.

## Implementation notes (kept in sync with the branch)

- Implemented as planned. The only additions beyond the text above are the
  two extra validation/test cases recorded in step 10 (non-integer cap
  rejection, exactly-at-the-cap boundary) and the launch-file row in
  Consequences.
- Verification: `./sensors_ws/build.sh sound_speed_bridge` then
  `./sensors_ws/test.sh sound_speed_bridge` —
  `Summary: 76 tests, 0 errors, 0 failures, 0 skipped` (49 before this
  branch). flake8 and pep257 are part of that suite and are clean.

## Estimated Scope

Single PR.
