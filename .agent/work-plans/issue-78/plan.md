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
> Revision 6 (2026-09-15) widens the scope a fourth time, on the same
> standing operator decision and a Copilot cross-confirmation: the
> callback-versus-shutdown guard [SW3] gave `garmin_sidescan` is extended
> to the publishing timers of the other three nodes, marked **[SW4]** —
> the residual Revision 5 recorded rather than assumed away.
>
> Revision 5 (2026-09-15) widens the scope once more, on the same
> operator decision: the third defect, found while verifying [SW2] and
> until now listed as out of scope — `garmin_sidescan`'s timer callback
> racing the context shutdown — is fixed here too, marked **[SW3]**.
>
> Revision 7 (2026-09-15) widens the scope a fifth time, on a **fresh**
> explicit operator instruction rather than the standing decision:
> `garmin_sidescan`'s `destroy_node()` does not join its daemon threads.
> Marked **[SW5]**; it was the first entry of the round-5 residual list,
> which now no longer carries it.

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

## Scope widening: five pre-existing defects, by operator decision

At the publish gate the operator was asked whether the pre-existing
defects the #78 reviews found should be filed as follow-ups or fixed here.
The decision, verbatim:

> "Fix those before publishing to reduce issue churn and get fixes done
> quicker."

All five are therefore in scope for this branch — [SW1] and [SW2] from
the reviews, [SW3], which surfaced while verifying [SW2], [SW4], the
residual [SW3] recorded, and [SW5], the first residual of pre-push round
5. The operator's quoted decision was given for [SW1]/[SW2]; the host
orchestrator applied that standing decision to [SW3] on its own
judgement, and the operator confirmed it after the PR was opened
(2026-09-15: "garmin fix is ok"). [SW4] is the same class of fix as the
[SW3] the operator confirmed, and Copilot's PR review raised it
independently (round 3, twice), cross-confirming the round-4 local review's
own residual — so the host applied the standing decision to it as well.
[SW5] needed no standing decision: the operator instructed it directly
(quoted in its own section below). None was introduced by #78; all sit on
its contract, which is why the reviews raised them.

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

### [SW4] The same race on the other three nodes' publishing timers

[SW3] fixed `garmin_sidescan`, where the race was actually observed, and
recorded the residual: `sound_speed_bridge` and `zda_serial_bridge` each
run a 1 Hz `_publish_diagnostics` timer and `kongsberg_em_bridge` a
SonarInfo heartbeat timer, so all three share the hazard in principle —
the signal handler tears the context down while the executor is still
inside `spin()`, and a timer that reaches `publish()` after that raises
`RCLError: Failed to publish: publisher's context is invalid` straight out
of `spin()`. It has never been observed on these three (their real-SIGINT
runs exit 0); it is a race, and [SW3]'s own real-SIGINT runs did not
reproduce it either on the node where it *was* seen.

Fix: the same guard, applied inline at the single publish call site in
each node rather than as a copy of `garmin_sidescan`'s decorator — that
node has ten guarded call sites and no shared Python package exists
between these four (`marine_tools` itself is `ament_cmake`/C++), so a
shared helper would mean a new cross-package runtime dependency for five
lines. The semantics are identical and deliberate: the **call** is
guarded, not preceded by an `if rclpy.ok()` check-then-act; `RCLError` and
`InvalidHandle` are caught; `rclpy.ok(context=self.context)` is consulted
only afterwards to decide what the failure meant; a shutdown in flight
returns quietly and a failure on a **live** context is re-raised, so a
genuine broken publisher is still loud.

`kongsberg_em_bridge`'s heartbeat already had a blanket
`except Exception` that warned and continued. That is narrowed, not
removed: the RCL class is now triaged against the context (quiet on a dead
one, re-raised on a live one — a publisher that has stopped working
mid-survey is not something a heartbeat should paper over), and any other
exception keeps the previous throttled-warning behaviour, so a non-RCL bug
still cannot kill the node.

#### [SW4] extension: the `sound_speed_bridge` reading path

Copilot's round-4 review cross-confirmed a suggestion pre-push round 1 had
deferred: the publishing *timer* was not the only unguarded RCL call site
in `sound_speed_bridge`. `_handle_reading()` runs on the **serial thread**
and publishes `sound_speed`, `raw` and the optional
`temperature`/`pressure`, then logs from the UDP error path. Its only
protection was a `self._stop_event.is_set()` test at the top, which is
check-then-act: a SIGINT landing in the gap between that test and any of
those publishes raises the same `RCLError` — and on this thread nothing
catches it, because `_serial_loop` catches only `(SerialException,
OSError)`. The reader thread therefore dies with a traceback on a
deliberate Ctrl-C.

Fix: the same [SW4] call-level guard, once, around the whole publishing
body. The body moves into a new `_publish_reading()` so the guard wraps a
single call rather than fifty lines; nothing else about the path changes.
This is recorded as an **extension of [SW4]**, not a new widening: it is
the same defect class, in the same package, under the same operator
standing decision, and it was raised by Copilot on the PR.

`_publish_serial_tap` needs no guard — it already carries a deliberately
broad `except` that counts and throttle-logs every failure (see its
docstring), so an RCL failure there is already isolated from the reader.

Regression test (`test/test_shutdown_guard.py`): the race is not
reproducible on demand, so the ordering is **forced** — a real shut-down
`Context` and a `sound_speed` publisher raising the exact field
`RCLError`. `_handle_reading` must then return quietly; the same error on
a **live** context must still propagate; a non-RCL error must propagate
either way; and `_serial_loop`, driven synchronously over three chunks,
must consume every one of them — a loop that exits early is exactly the
field failure (silent sensor, `serial_connected` still true) the guard
exists to prevent.

### [SW5] `garmin_sidescan.destroy_node()` does not join its daemon threads

Raised as a suggestion by pre-push rounds 4 and 5 and then instructed
directly by the operator (2026-09-15):

> "fix the garmin's destroy_node issue"

`destroy_node()` set `self._running = False`, sent transmit OFF and called
`super().destroy_node()` without waiting for any of the node's **four**
daemon threads: `_rx_thread` (`gcv_rx`), the two `_aux_loop` listeners
(`gcv_status`, `gcv_config`) and the startup thread (`gcv_startup`). Two
of them were anonymous — started and never referenced — so they could not
have been joined even if the code had tried.

Every one of them publishes: imagery, nadir range and water temperature
from `_rx_loop`, the raw status/config captures from the aux loops,
transmit state from the startup thread. A thread still running when the
node's publishers are destroyed therefore keeps working against a
torn-down node — and [SW3]'s `quiet_on_shutdown` makes that *quiet* on a
dead context, which is right for a signal teardown and wrong as a way to
leave a thread running. Worse, the startup thread issues **commands**: a
`transmit_on_startup` ON sent after the shutdown OFF leaves the sonar
pinging unattended, which is the one failure this node's shutdown path
exists to prevent.

Fix, following `sound_speed_bridge`'s pattern (`node.py:789-796`: signal
stop, bounded `join`, then `super().destroy_node()`):

- **One stop signal.** `self._running` (a bool checked only at the top of
  each loop) becomes `self._stop_event`, a `threading.Event` — the same
  name and shape as `sound_speed_bridge`'s serial thread. The bool could
  not unblock anything; the event can, and there is no second flag to
  fall out of step with it.
- **Every thread is an attribute** (`_rx_thread`, `_aux_threads`,
  `_startup_thread`), so it can be joined.
- **Ordering: joins first, transmit OFF after them, `super()` last.** The
  OFF is sent after the joins so it is the last command on the wire and
  the startup thread cannot override it. Nothing is lost by waiting: an
  OFF is confirmed by `_send`'s own TCP `sendall` (a 2 s-timeout socket
  opened per command), not by anything the receive loops decode — they
  carry imagery and the device status flag, and `destroy_node` consults
  neither. The existing three-attempt OFF retry and its ERROR are
  unchanged.
- **Bounded joins**: `SHUTDOWN_JOIN_TIMEOUT_S = 5.0`, a budget for the
  **whole set** (each join gets what is left of it), so several wedged
  sockets cannot multiply it. 4.0 s is the longest blocking call any
  worker can be inside — `_send`'s 2.0 s TCP socket timeout applies to
  the connect *and* again to the `sendall`, on the startup thread —
  plus a second of scheduling slack; the receive loops block at most on
  `_open_mcast`'s 1.0 s `settimeout`. *(corrected in #92: this arithmetic
  was written against an earlier 3.0 s budget and counted one 2.0 s
  timeout, not two; 5.0 s is what shipped.)* A thread that
  misses the budget is named in a WARN and, being a daemon, dies with the
  process: a wedged socket read delays shutdown by a bounded interval and
  never hangs it.
- **Unblocking, by shortening the blocking interval rather than closing
  the socket.** The receive loops already cap their blocking read at the
  1.0 s socket timeout, so they need nothing; what stalled them was the
  *back-off*, a plain `time.sleep(2.0)` on the multicast-rejoin path
  (`_rx_loop`, `_aux_loop`) and `time.sleep(0.3)` between the startup
  OFF repeats. Those become `self._stop_event.wait(...)`, which returns
  the moment the stop is signalled. Closing the sockets from
  `destroy_node()` was rejected: they are locals owned by the loops,
  which close and reopen them on every `OSError`, so a cross-thread close
  would race the reopen and hand the loop a closed fd — with no benefit,
  since the 1.0 s timeout already bounds the read.
- **The startup thread returns early** once the stop is signalled, before
  its range command and before a `transmit_on_startup` ON, so a shutdown
  landing mid-startup cannot put a command on the wire behind the
  shutdown OFF.

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

- **Default 4096 (4 KiB)**: 16× the 256 B serial read chunk, >100× the
  sentences of the protocols in use (AML ~11 B, BizzyBoat `$AML,SVM`
  ~32 B), and ~5 s of wire at 800 B/s. It bounds the multi-MB hazard by
  roughly three orders of magnitude. It is a default, not a bound on
  sentence length — `regex_pattern` bounds nothing, so a protocol with
  longer lines needs a correspondingly larger cap.
- **Floor 256, `ValueError` below it**: 256 B is the serial read size
  (`node.py:178`). Below that floor, every single read chunk would
  overflow the cap even in healthy traffic. The floor is a sanity bound,
  not a line-length guarantee: `regex_pattern` bounds nothing, so the cap
  must be sized above the longest legitimate sentence of the configured
  protocol (AML ~11 B, BizzyBoat `$AML,SVM` ~32 B; the 4096 default leaves
  >100x margin). What an undersized cap costs is timing-dependent, which
  is why it is worth sizing for: the cap bounds residue *after* framing,
  so a line longer than the cap still frames whenever it and its
  terminator arrive within one `feed()`, and only residue that reaches
  the cap *before* a terminator is seen — a long sentence split across
  reads, or a stream that has stopped terminating — is trimmed and then
  discarded through the next terminator by `_resync`. An undersized cap
  therefore loses whichever sentences happen to straddle a read boundary,
  rather than failing cleanly. Sized above the longest sentence,
  trimming can only ever be triggered by unframed residue, i.e.
  by a genuine framing stall. Validated identically in the ABC and at
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
  sustained stall at the same byte total.

The two are a *correlated pair* written on the serial thread and read on
the diagnostics timer, so they are **one tuple**, rebound in a single
GIL-atomic assignment per trim event and exposed as the `trim_stats`
snapshot; `buffer_dropped_bytes` / `buffer_trim_count` remain as
read-only views over it **[PR-R3 should-fix, `36989be`]**.
`_publish_diagnostics` reads the tuple **once** per tick and uses that
snapshot for the WARN text and the KeyValues alike **[PR-R1-S5]**, so
the WARN can never quote a trim count without the bytes that went with
it. (Rev 2 had two plain ints read separately; Copilot round 3 showed the
timer could observe a torn pair — a count without its bytes — because
`_trim_residue()` updated them in two statements.)

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
  `parser_max_buffer_bytes` parameter row **and** the two diagnostics
  `KeyValue`s this change adds, `buffer_dropped_bytes` and
  `buffer_trim_count` **[PR-F12c]**. #88's ask list names all three
  explicitly, so the deferral points at a list that actually carries them.
- **rolker/marine_tools#77 / PR #89** — byte-exact wire tap, the recovery
  path for bytes this cap drops.

## Files to Change

| File | Change |
|------|--------|
| `sound_speed_bridge/launch/aml_svs.launch.py` | Add the `parser_max_buffer_bytes` launch argument at its default **[PR-R1-S8]** |
| `sound_speed_bridge/sound_speed_bridge/parsers.py` | **[SW1]** Non-finite guard: both `_parse`s treat `nan`/`inf`/`-inf`/`snan`/`1e999` as a parse failure (NaN reading, `raw_mm_s=None`), every numeric conversion inside the guarded region; `RegexParser._optional_float` drops a non-finite temperature/pressure; module docstring says so. Plus: move `_buffer` into the `SoundSpeedParser` ABC with `_max_buffer_bytes`, `_discarding`, `buffer_dropped_bytes`, `buffer_trim_count`; add `_resync()` + `_trim_residue()` helpers and floor validation; both `feed()`s become eager, trim residue at the end, and resync after a trim; `max_buffer_bytes` on both constructors; `PARSERS` factories pass it; module + ABC docstrings; since round 3 the guard is on the **mm/s product** (`value * 1000.0`), not the m/s value alone, in both parsers; trim counters are one atomic tuple (`trim_stats`) |
| `sound_speed_bridge/test/test_shutdown_guard.py` | **[SW4]** new: `test_diagnostics_is_quiet_once_the_context_is_shut_down`, `test_a_publish_failure_on_a_live_context_is_still_raised`, `test_a_non_rcl_error_is_not_swallowed_by_the_shutdown_guard`, each against a real shut-down `Context`; **[SW4] extension** `test_a_reading_publish_is_quiet_once_the_context_is_shut_down`, `test_a_reading_publish_failure_on_a_live_context_is_still_raised`, `test_a_non_rcl_error_on_the_reading_path_is_not_swallowed`, `test_the_serial_loop_survives_a_shutdown_race_on_a_publish` |
| `sound_speed_bridge/sound_speed_bridge/node.py` | `declare_parameter('parser_max_buffer_bytes', 4096)` with a `read_only=True` descriptor **[PR-R1-S3]** + floor validation; node construction moved inside `main()`'s `try` so the refusal is one FATAL line **[PR-R1-S7]**, exiting **1** via `SystemExit` so `ros2 launch`/systemd see a failure, with the `except` scoped to construction only, not `spin()` **[PR-R2-MF1, PR-R2-S1]**; `_last_buffer_trim_count`, `_last_warned_dropped_bytes`, back-off state; backed-off WARN in `_publish_diagnostics`; two new `KeyValue`s; **[SW2]** `main()` catches `ExternalShutdownException` and shuts down via `rclpy.try_shutdown()`; **[SW4]** shutdown guard around the `_publish_diagnostics` publish, and (the [SW4] extension) around the reading path, whose body moves into `_publish_reading()` |
| `sound_speed_bridge/test/test_parsers.py` | AML cap tests: bound, drop-oldest + no-fragment, resync, `\n`-padding boundary, no spurious trim on an oversize healthy chunk, invalid cap; **[SW1]** `test_aml_non_finite_sentence_is_a_parse_failure` (parametrized over `nan`/`NaN`/`inf`/`-inf`/`Infinity`/`snan`/`1e999`/`-1e999`) and `test_aml_keeps_framing_after_a_non_finite_sentence` |
| `sound_speed_bridge/test/test_regex_parser.py` | Same set for `RegexParser`, plus the CRLF straddle and the `search`-matches-a-fragment case; **[SW1]** `test_regex_non_finite_capture_is_a_parse_failure`, `test_regex_non_finite_scale_product_is_a_parse_failure`, `test_regex_non_finite_optional_fields_are_not_reported`, `test_regex_keeps_framing_after_a_non_finite_sentence` |
| `sound_speed_bridge/test/test_node.py` | Parameter validation (`test_parser_max_buffer_bytes_reaches_the_parser`, `test_parser_max_buffer_bytes_below_floor_is_rejected`, `test_parser_max_buffer_bytes_is_read_only` **[PR-R1-S3]**, `test_main_reports_a_rejected_parameter_and_shuts_down` **[PR-R1-S7, PR-R2-MF1]**); counters in `/diagnostics` (`test_buffer_counters_surface_in_diagnostics`, `test_trim_counters_are_snapshotted_once_per_tick` **[PR-R1-S5]**); WARN once then backed off (`test_buffer_trim_warns_once_then_backs_off`, `test_buffer_trim_warn_backoff_resets_after_a_quiet_period` **[PR-R1-S4]**); **[SW1]** `test_serial_thread_survives_a_non_finite_sentence` (drives the real `_serial_loop`); **[SW2]** `test_sigint_exits_zero_without_a_traceback` (real SIGINT, subprocess) and `test_main_returns_cleanly_on_an_external_shutdown`; **[PR-R3-S2]** `test_a_valueerror_from_spin_is_not_reported_as_a_start_failure` pins the narrowed `except` scope |
| `sound_speed_bridge/package.xml` | `<depend>rcl_interfaces</depend>` for `ParameterDescriptor` **[PR-R2-S3]** |
| `sound_speed_bridge/sound_speed_bridge/sinks.py` | **[PR-R2/R3 must-fix]** both formatters compute the mm/s product and validate finiteness **unconditionally, before** choosing the raw-integer path — a finite 1e306 m/s is inf mm/s; `round(inf)` raised on the serial thread and `{value_mm_s}` could interpolate inf; NaN is skipped whether or not `raw_mm_s` is present |
| `sound_speed_bridge/test/test_sinks.py` | overflow-skip tests for both formatters, NaN-with-`raw_mm_s` skip, finite-but-huge-with-`raw_mm_s` skip |
| `zda_serial_bridge/zda_serial_bridge/node.py` | **[SW2]** same `main()` fix; **[SW4]** shutdown guard around the `_publish_diagnostics` publish |
| `zda_serial_bridge/test/test_node.py` | **[SW2]** `test_sigint_exits_zero_without_a_traceback` (real SIGINT, subprocess) |
| `zda_serial_bridge/test/test_shutdown_guard.py` | **[SW4]** new: `test_diagnostics_is_quiet_once_the_context_is_shut_down`, `test_a_publish_failure_on_a_live_context_is_still_raised`, `test_a_non_rcl_error_is_not_swallowed_by_the_shutdown_guard`, each against a real shut-down `Context` |
| `kongsberg_em_bridge/kongsberg_em_bridge/node.py` | **[SW2]** same `main()` fix (its `if rclpy.ok(): rclpy.shutdown()` becomes `try_shutdown()`); **[SW4]** shutdown guard in `_sonar_info_heartbeat`, narrowing its blanket `except Exception` |
| `kongsberg_em_bridge/test/test_main_shutdown.py` | **[SW2]** new: `test_main_returns_cleanly_on_an_external_shutdown` (node mocked; only `main()` is under test) and `test_sigint_exits_zero_without_a_traceback` (real entry point, real SIGINT, subprocess, UDP socket mocked so nothing binds port 20002); **[SW4]** `test_heartbeat_is_quiet_once_the_context_is_shut_down`, `test_a_publish_failure_on_a_live_context_is_still_raised`, `test_a_non_rcl_error_is_still_warned_and_not_propagated` |
| `garmin_sidescan/garmin_sidescan/node.py` | **[SW2]** same `main()` fix; **[SW3]** new `quiet_on_shutdown` decorator, applied to the three timer callbacks, the `~/change_state` subscription callback, `_publish_tx_state` / `_publish_control_set` and the `_rx_loop` / `_aux_loop` thread entries; **[SW5]** `_running` bool → `_stop_event` `threading.Event`, all four worker threads kept as attributes (`_rx_thread`, `_aux_threads`, `_startup_thread`), new `SHUTDOWN_JOIN_TIMEOUT_S` + `_join_workers()`, `destroy_node()` joins before sending transmit OFF and before `super()`, every back-off sleep becomes an interruptible `_stop_event.wait`, and the startup thread returns early once the stop is signalled |
| `garmin_sidescan/test/test_main_shutdown.py` | **[SW2]** new: same minimal `main()`-level test, plus `test_sigint_exits_zero_without_a_traceback` — the real entry point under a real SIGINT in a subprocess, sockets mocked, matching the harness the other packages carry; **[SW3]** four callback-level tests on a real shut-down `Context` — `test_reconcile_is_quiet_once_the_context_is_shut_down`, `test_a_publish_timer_is_quiet_once_the_context_is_shut_down`, `test_a_publish_failure_on_a_live_context_is_still_raised`, `test_a_non_rcl_error_is_not_swallowed_by_the_shutdown_guard` |
| `garmin_sidescan/test/test_shutdown_joins.py` | **[SW5]** new: `test_destroy_node_joins_every_worker_thread`, `test_a_wedged_thread_does_not_hang_destroy_node`, `test_a_reconnecting_thread_is_joined_promptly`, `test_transmit_off_is_sent_after_every_thread_is_joined`, `test_destroy_node_still_retries_a_failing_transmit_off`, `test_startup_thread_issues_no_command_once_the_stop_is_signalled` — a real node with its sockets faked out, plus one fake-node case for the startup early return |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Test what breaks | Step 10 covers cap enforcement, the fragment hazard (the *reason* for the cap, and the one that can publish a wrong value), the trim-on-append data-loss regression, straddle boundaries, resync, and the WARN back-off. |
| A change includes its consequences | `/diagnostics` KeyValues and parser docstrings land in the same PR; the README row is explicitly routed to #88 rather than left implicit; #90 is filed rather than silently absorbed. |
| Human control and transparency | The parameter is declared, defaulted, floor-validated and justified; trims are visible as both bytes and events, and as a WARN that is loud once and then quiet. The plan states what the cap *loses*, not only what it bounds. |
| Only what's needed | Shared logic in the ABC, two ints of counter state, no new topic, no new timer, no per-parser trim strategy. |
| Improve incrementally | No unrelated refactors; #90 and #88 stay separate. The original #78 change was two source files and three test files, all in `sound_speed_bridge`; the operator's scope widening carried it to **four packages** — `sound_speed_bridge`, `zda_serial_bridge`, `kongsberg_em_bridge` and `garmin_sidescan` — each of which gains only the shutdown-contract fix and its tests (see Files to Change). |

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
| **[SW3]** callback-versus-shutdown guard | `garmin_sidescan` only. No launch file, parameter, topic or service changes, and with a live context every guarded callback behaves exactly as before. The residual this row recorded — the other three nodes' publishing timers sharing the race in principle — is **no longer residual**: it is fixed as [SW4] below | Yes — Scope widening |
| **[SW4]** the same guard on the other three nodes | One publish call site per node (`sound_speed_bridge`/`zda_serial_bridge` `_publish_diagnostics`, `kongsberg_em_bridge` `_sonar_info_heartbeat`), **plus the [SW4] extension**: `sound_speed_bridge`'s serial-thread reading path, whose body moves into `_publish_reading()` so one guard covers `sound_speed`, `raw`, `temperature`/`pressure` and the UDP error path's logging. No launch file, parameter, topic or service changes; with a live context every callback behaves exactly as before. One behaviour change, deliberate: `kongsberg_em_bridge`'s heartbeat used to swallow-and-warn **every** exception, and an RCL failure on a live context is now re-raised instead — a publisher that has stopped working mid-survey must not be papered over. No shared helper: the four packages share no Python package (`marine_tools` is `ament_cmake`/C++), so a helper would add a cross-package runtime dependency for five lines | Yes — Scope widening |
| **[SW5]** `garmin_sidescan.destroy_node()` joins its threads | `garmin_sidescan` only. No launch file, parameter, topic, service or message change; the transmit-OFF retry and its ERROR are unchanged, and the only new operator-visible output is a WARN naming a thread that missed the join budget. Two deliberate behaviour changes: shutdown now takes up to `SHUTDOWN_JOIN_TIMEOUT_S` (5.0 s — *corrected in #92*; 3.0 s was the value this row was written against) longer when a socket read is wedged — bounded, and the threads are daemons so the process still exits — and the transmit OFF is now sent *after* the joins rather than first, which is what stops the startup thread overriding it. `self._running` is gone; nothing outside `node.py` referenced it | Yes — Scope widening |
| **[SW1]**/**[SW2]**/**[SW3]**/**[SW4]**/**[SW5]** fixed here rather than filed | The operator's publish-gate decision for the first four and a direct instruction for [SW5], both quoted in Scope widening; no follow-up issues filed for these five | Yes |
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

## Residuals after pre-push round 5 (recorded, not done — each is a further widening or a polish item for the operator's call)

> The first residual — `destroy_node()` not joining its daemon threads —
> was instructed by the operator and is fixed on this branch as **[SW5]**
> (there were four threads, not three), so it is no longer listed here.
> Two more are gone the same way: Copilot's round-4 review cross-confirmed
> the missing real-SIGINT tests for `garmin_sidescan` and
> `kongsberg_em_bridge` (now committed, so all four packages carry the
> harness) and the `quiet_on_shutdown` docstring caveat (now written), so
> nothing remains in this list.

- `quiet_on_shutdown`'s docstring says a genuine fault stays loud; a real `RCLError` that coincides with a shutdown is swallowed — caveat owed in the docstring.

## Residual after pre-push round 6 (recorded, not done — a sixth widening for the operator's call)

- `garmin_sidescan`'s `main()` constructs the node outside its `try`, so a construction failure is a raw traceback (not the one FATAL line `sound_speed_bridge` now gives) and `_join_workers()` could dereference thread attributes that were never created if construction failed part-way. Unreachable today via `destroy_node()` because construction failure never reaches it; fixing it means restructuring garmin's `main()` as was done for `sound_speed_bridge`.

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
  to Change. [SW3] landed as one more, in `garmin_sidescan` alone, and
  [SW4] as one more again, in the other three nodes. [SW5] landed as one
  more after that, in `garmin_sidescan` alone.
- Verification: all four touched packages are built and tested
  (`./sensors_ws/build.sh` / `./sensors_ws/test.sh` with the four package
  names). flake8 and pep257 are part of each suite and are clean. The
  per-package summary lines are recorded in the `## Implementation`
  progress entry for this pass.
- [SW2] is additionally verified by *execution*, not only by test: the
  console entry points are run with mocked I/O and sent a real SIGINT.
  All four — `sound_speed_bridge`, `zda_serial_bridge`,
  `kongsberg_em_bridge` and `garmin_sidescan` — exit **0** with zero
  traceback lines (every one of them exited 1 before the fix). Since
  Copilot's round-4 review that execution is **committed as a test** in
  all four packages, not only run by hand: each carries
  `test_sigint_exits_zero_without_a_traceback`, and each was mutation-
  checked by restoring the pre-fix `main()` in an out-of-tree copy.

## Estimated Scope

Single PR.
