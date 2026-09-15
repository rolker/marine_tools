# Plan: parsers: unbounded accumulation buffer — cap + trim WARN

## Issue

https://github.com/rolker/marine_tools/issues/78

> Revision 2 (2026-09-15) resolves all 5 must-fix and 7 should-fix findings
> of the Plan Review entry in `progress.md`. Each resolution is marked
> **[PR-F*n*]** against the finding it answers, with the rationale inline.
>
> Revision 3 (2026-09-15) syncs the plan with the fixes made for the
> round-1 Local Review (Pre-Push); those are marked **[PR-R1-MF*n*]** /
> **[PR-R1-S*n*]** against that entry's must-fix and suggestion numbering.
>
> Revision 4 (2026-09-15) does two things. It defines the markers the
> table already carried but no note explained: **[PR-R2-MF1]** /
> **[PR-R2-S*n*]** are the round-2 Local Review's must-fix and
> suggestions, **[PR-R3-S*n*]** the round-3 ones. And it records the
> operator's scope widening (below) — the two pre-existing defects the
> reviews surfaced are now fixed on this branch, marked **[SW1]** and
> **[SW2]**.
>
> Revision 5 (2026-09-15) widens the scope once more, on the same
> operator decision: the third defect, found while verifying [SW2] and
> until now listed as out of scope — `garmin_sidescan`'s timer callback
> racing the context shutdown — is fixed here too, marked **[SW3]**.

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

## Scope widening: three pre-existing defects, by operator decision

At the publish gate the operator was asked whether the pre-existing
defects the #78 reviews found should be filed as follow-ups or fixed here.
The decision, verbatim:

> "Fix those before publishing to reduce issue churn and get fixes done
> quicker."

All three are therefore in scope for this branch — [SW1] and [SW2] from
the reviews, and [SW3], which surfaced while verifying [SW2]. The
operator's quoted decision was given for [SW1]/[SW2]; the host
orchestrator applied that standing decision to [SW3] on its own
judgement, and the operator confirmed it after the PR was opened
(2026-09-15: "garmin fix is ok"). None was introduced by #78; all sit on its
contract, which is why the reviews raised them.

### [SW1] A non-finite sentence kills the serial reader thread

`AMLParser._parse` converted the parsed `Decimal` to integer mm/s
**outside** its `try`. `nan`, `inf`, `-inf` and `snan` are all valid
`Decimal` literals, and `1e999` is a *finite* Decimal that overflows to an
infinite float, so one such sentence raised
`InvalidOperation`/`OverflowError`/`ValueError` straight out of `feed()`.
`_serial_loop` catches only `(SerialException, OSError)`, so the exception
ended the daemon reader thread with `_serial_connected` still **True**:
the node reports a healthy serial link and never publishes another reading
for the rest of the deployment. Eager `feed()` (step 2) widens the loss
from one sentence to the whole read chunk, which is what put it on this
branch's contract.

Fix: both numeric conversions move inside the guarded region and the
result is checked for finiteness; a sentence that cannot yield a finite
number is a parse failure like any other — NaN reading, `raw_bytes`
preserved, counted by the existing parse-error path.

`RegexParser` has the same class of hazard one step downstream, and it was
checked as the review asked: `float()` accepts `'nan'`/`'inf'` and
overflows `'1e999'`, so a loose pattern published an **infinite** sound
speed. That is not merely wrong downstream — `format_valeport` and
`format_template` `round()` it *on the serial thread*, raising
`OverflowError` past the same catch, so the thread dies there instead. A
non-finite value (including one produced by `sound_speed_scale`) is
therefore NaN too. Optional `temperature`/`pressure` captures follow the
same rule: an unreported field beats `inf` in a `Temperature` /
`FluidPressure` message.

### [SW2] A deliberate SIGINT stop exits 1

All four nodes in this repo share one `main()` shape. rclpy installs its
own SIGINT handler, which shuts the context down *before* `main()` sees
anything, so a deliberate stop produced two failures at once: `spin()`
raised an uncaught `ExternalShutdownException`, and the `finally`'s
`rclpy.shutdown()` raised `RCLError: rcl_shutdown already called`. Exit
status **1**, two tracebacks. Under systemd `Restart=on-failure` an
operator stopping a node is then indistinguishable from a crash — and
this branch just made a refused parameter exit 1, a signal that noise
would bury.

Fix, in `sound_speed_bridge`, `zda_serial_bridge`, `kongsberg_em_bridge`
and `garmin_sidescan`: catch `ExternalShutdownException` alongside
`KeyboardInterrupt`, and shut down through the idempotent
`rclpy.try_shutdown()`. `destroy_node()` stays where it is — verified by
execution to run correctly on an already-shut-down context.
`kongsberg_em_bridge`'s `if rclpy.ok(): rclpy.shutdown()` becomes
`try_shutdown()` as well: same intent, without the check-then-act race.
Nothing else in those nodes changes.

### [SW3] `garmin_sidescan` still exits 1 on SIGINT — one layer in

Fixing `main()` was not enough for this node. Verifying [SW2] by
execution showed `garmin_sidescan` still exiting **1** on a deliberate
SIGINT, for a different reason: the shutdown lands while the executor is
still inside `spin()`, so the `_reconcile_transmit_param` **timer
callback** reaches `set_parameters()` — and the parameter-event publish
inside it — on a context that is already down. rcl raises `RCLError:
Failed to publish: publisher's context is invalid`, `spin()` propagates
it, and the operator's clean stop is again a traceback and exit 1 under
`Restart=on-failure`. The same hazard sits on every sibling callback that
publishes (`_publish_status`, `_publish_diagnostics`, `_on_control_value`)
and on the driver's own daemon threads, which publish imagery, nadir
range, temperature and transmit state.

Fix: a `quiet_on_shutdown` decorator on those methods. It wraps the
**call** — a bare `if rclpy.ok()` before it would be check-then-act, and
the shutdown can land in the gap — catching `RCLError`/`InvalidHandle`
and consulting `rclpy.ok(context=self.context)` only afterwards, to
decide what the failure meant: a shutdown in flight returns quietly, a
failure on a live context is re-raised unchanged. The node's normal
behaviour is untouched: with a live context every callback runs and
raises exactly as before. Applied to the three timer callbacks, the
`~/change_state` subscription callback, the two shared publish helpers
(`_publish_tx_state`, `_publish_control_set`, which the startup daemon
thread and the `~/set_transmit` service reach) and the two receive-loop
thread entries (`_rx_loop`, `_aux_loop`) — where a stray `RCLError` at
shutdown would otherwise kill a daemon thread with a traceback on stderr.

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
| `buffer_dropped_bytes` | total bytes that never became a reading — trimmed off the residue **plus** the head fragment `_resync()` discards through the next terminator (public, polled) |
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
  stream was lost — and is what the WARN text quotes. It counts the
  resync discard as well as the trim, so it matches that contract
  **[PR-R1-MF1]**.
- `buffer_trim_count` is kept because it is the cheap **edge detector**
  the node's back-off needs ("has any new trim happened since the last
  tick?"), and because it distinguishes one large overflow from a
  sustained stall at the same byte total. Two ints; no further state.

Both are plain public attributes polled by `_publish_diagnostics`, matching
how it already polls `self._rate_hz` and `self._parse_error_count` rather
than being pushed updates. Each individual cross-thread read of a Python
int is atomic under the GIL — the same assumption the existing counters
already make — but the two are a *correlated pair*, so
`_publish_diagnostics` reads both **once** per tick and uses that one
snapshot for the WARN text and the KeyValues alike **[PR-R1-S5]**.

### 7. WARN: first trim immediately, then exponential back-off **[PR-F8]**

Rev 1's 1 Hz WARN is ~18k lines over a 5 h stall, while
`_publish_diagnostics` is already reporting ERROR (stale reading)
throughout that window. Rule, evaluated on the existing 1 Hz diagnostics
timer (no new timer):

- the **first** trim WARNs immediately (interval starts at 0 s);
- after each WARN the minimum interval doubles: 1, 2, 4, … s, **capped at
  300 s**;
- if a tick sees no new trims and the **last trim observed** is older
  than the 300 s cap, the interval resets to 0 so a *later, separate*
  stall warns promptly again. The anchor is the last trim, not the last
  WARN: a stall still trimming inside the back-off must never look quiet
  **[PR-R1-S4]**.

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
parameter row for `parser_max_buffer_bytes` and the two diagnostics
`KeyValue`s (`buffer_dropped_bytes`, `buffer_trim_count`) are **deferred
to rolker/marine_tools#88**, which creates that README **[PR-F12c]**; #78
does not create a README solely to hold them. #88's ask list now names
all three explicitly, so the deferral points at a list that actually
carries them
(https://github.com/rolker/marine_tools/issues/88#issuecomment-5682117811).

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
| `sound_speed_bridge/launch/aml_svs.launch.py` | Add the `parser_max_buffer_bytes` launch argument at its default **[PR-R1-S8]** |
| `sound_speed_bridge/sound_speed_bridge/parsers.py` | **[SW1]** Non-finite guard: both `_parse`s treat `nan`/`inf`/`-inf`/`snan`/`1e999` as a parse failure (NaN reading, `raw_mm_s=None`), every numeric conversion inside the guarded region; `RegexParser._optional_float` drops a non-finite temperature/pressure; module docstring says so. Plus: move `_buffer` into the `SoundSpeedParser` ABC with `_max_buffer_bytes`, `_discarding`, `buffer_dropped_bytes`, `buffer_trim_count`; add `_resync()` + `_trim_residue()` helpers and floor validation; both `feed()`s become eager, trim residue at the end, and resync after a trim; `max_buffer_bytes` on both constructors; `PARSERS` factories pass it; module + ABC docstrings |
| `sound_speed_bridge/sound_speed_bridge/node.py` | `declare_parameter('parser_max_buffer_bytes', 4096)` with a `read_only=True` descriptor **[PR-R1-S3]** + floor validation; node construction moved inside `main()`'s `try` so the refusal is one FATAL line **[PR-R1-S7]**, exiting **1** via `SystemExit` so `ros2 launch`/systemd see a failure, with the `except` scoped to construction only, not `spin()` **[PR-R2-MF1, PR-R2-S1]**; `_last_buffer_trim_count`, `_last_warned_dropped_bytes`, back-off state; backed-off WARN in `_publish_diagnostics`; two new `KeyValue`s; **[SW2]** `main()` catches `ExternalShutdownException` and shuts down via `rclpy.try_shutdown()` |
| `sound_speed_bridge/test/test_parsers.py` | AML cap tests: bound, drop-oldest + no-fragment, resync, `\n`-padding boundary, no spurious trim on an oversize healthy chunk, invalid cap; **[SW1]** `test_aml_non_finite_sentence_is_a_parse_failure` (parametrized over `nan`/`NaN`/`inf`/`-inf`/`Infinity`/`snan`/`1e999`/`-1e999`) and `test_aml_keeps_framing_after_a_non_finite_sentence` |
| `sound_speed_bridge/test/test_regex_parser.py` | Same set for `RegexParser`, plus the CRLF straddle and the `search`-matches-a-fragment case; **[SW1]** `test_regex_non_finite_capture_is_a_parse_failure`, `test_regex_non_finite_scale_product_is_a_parse_failure`, `test_regex_non_finite_optional_fields_are_not_reported`, `test_regex_keeps_framing_after_a_non_finite_sentence` |
| `sound_speed_bridge/test/test_node.py` | Parameter validation (`test_parser_max_buffer_bytes_reaches_the_parser`, `test_parser_max_buffer_bytes_below_floor_is_rejected`, `test_parser_max_buffer_bytes_is_read_only` **[PR-R1-S3]**, `test_main_reports_a_rejected_parameter_and_shuts_down` **[PR-R1-S7, PR-R2-MF1]**); counters in `/diagnostics` (`test_buffer_counters_surface_in_diagnostics`, `test_trim_counters_are_snapshotted_once_per_tick` **[PR-R1-S5]**); WARN once then backed off (`test_buffer_trim_warns_once_then_backs_off`, `test_buffer_trim_warn_backoff_resets_after_a_quiet_period` **[PR-R1-S4]**); **[SW1]** `test_serial_thread_survives_a_non_finite_sentence` (drives the real `_serial_loop`); **[SW2]** `test_sigint_exits_zero_without_a_traceback` (real SIGINT, subprocess) and `test_main_returns_cleanly_on_an_external_shutdown`; **[PR-R3-S2]** `test_a_valueerror_from_spin_is_not_reported_as_a_start_failure` pins the narrowed `except` scope |
| `sound_speed_bridge/package.xml` | `<depend>rcl_interfaces</depend>` for `ParameterDescriptor` **[PR-R2-S3]** |
| `zda_serial_bridge/zda_serial_bridge/node.py` | **[SW2]** same `main()` fix |
| `zda_serial_bridge/test/test_node.py` | **[SW2]** `test_sigint_exits_zero_without_a_traceback` (real SIGINT, subprocess) |
| `kongsberg_em_bridge/kongsberg_em_bridge/node.py` | **[SW2]** same `main()` fix (its `if rclpy.ok(): rclpy.shutdown()` becomes `try_shutdown()`) |
| `kongsberg_em_bridge/test/test_main_shutdown.py` | **[SW2]** new: `test_main_returns_cleanly_on_an_external_shutdown` (node mocked; only `main()` is under test) |
| `garmin_sidescan/garmin_sidescan/node.py` | **[SW2]** same `main()` fix; **[SW3]** new `quiet_on_shutdown` decorator, applied to the three timer callbacks, the `~/change_state` subscription callback, `_publish_tx_state` / `_publish_control_set` and the `_rx_loop` / `_aux_loop` thread entries |
| `garmin_sidescan/test/test_main_shutdown.py` | **[SW2]** new: same minimal `main()`-level test; **[SW3]** four callback-level tests on a real shut-down `Context` — `test_reconcile_is_quiet_once_the_context_is_shut_down`, `test_a_publish_timer_is_quiet_once_the_context_is_shut_down`, `test_a_publish_failure_on_a_live_context_is_still_raised`, `test_a_non_rcl_error_is_not_swallowed_by_the_shutdown_guard` |

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
| **[SW1]** parser rejects non-finite values | `parsers.py` module docstring; the Valeport/template UDP formatters were checked (they `round()` on the serial thread, which is why `RegexParser` converts `inf` to NaN rather than passing it on) and the diagnostics comparisons (a NaN last reading is already a WARN state) | Yes — Scope widening |
| **[SW2]** `main()` shutdown contract | All four nodes in the repo share the pattern, so all four are fixed in one commit; no launch file, parameter or topic changes | Yes — Scope widening |
| **[SW3]** callback-versus-shutdown guard | `garmin_sidescan` only. No launch file, parameter, topic or service changes, and with a live context every guarded callback behaves exactly as before. **Residual, stated rather than assumed**: the other three nodes each run a publishing timer (`_publish_diagnostics`, `_sonar_info_heartbeat`) and so share the same race in principle; it was never observed on them (their real-SIGINT runs exit 0), and extending the guard there is a fourth widening the operator has not been asked for — surfaced, not assumed away | Yes — Scope widening |
| **[SW1]**/**[SW2]**/**[SW3]** fixed here rather than filed | The operator's publish-gate decision, quoted in Scope widening; no follow-up issues filed for these three | Yes |
| A new node parameter | Package README parameter table | Deferred to #88 (no README exists yet) — step 9 |
| A new node parameter | `launch/aml_svs.launch.py` | Yes — the example launch surfaces the operator-tunable parameters, so `parser_max_buffer_bytes` is added as a launch argument at its 4096 default **[PR-R1-S8]** |

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
- The scope widening above ([SW1], [SW2]) landed as two further commits;
  [SW2] is why three packages beyond `sound_speed_bridge` appear in Files
  to Change. [SW3] landed as one more, in `garmin_sidescan` alone.
- Verification: all four touched packages are built and tested
  (`./sensors_ws/build.sh` / `./sensors_ws/test.sh` with the four package
  names). flake8 and pep257 are part of each suite and are clean. The
  per-package summary lines are recorded in the `## Implementation`
  progress entry for this pass.
- [SW2] is additionally verified by *execution*, not only by test: the
  console entry points are run with mocked I/O and sent a real SIGINT.
  `sound_speed_bridge`, `zda_serial_bridge` and `kongsberg_em_bridge` exit
  **0** with zero traceback lines (all three exited 1 before the fix).

## Estimated Scope

Single PR.
