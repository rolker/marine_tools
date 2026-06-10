---
issue: 28
---

# Issue #28 — garmin proxy: relay :50050 status + :51000 config to ROS host

## Integrated Review
**Status**: complete
**When**: 2026-06-10 11:47 -04:00
**By**: Claude Code Agent (Claude Opus 4.8, 1M context)

**PR**: #29 at `42a5909`
**Sources**: 1 (Copilot R1 @ `42a5909`) + CI rollup
**Cross-source confirmations**: 0
**CI**: all-pass (build-and-test SUCCESS)

### Findings
- [x] (minor, Copilot) `_relay_loop` `logged` init `-1` emits one idle "0 pkts" line, contradicting the "only when something moved" comment — init to `0` (`.py` + `.ps1`).
- [x] (valid, Copilot) `.ps1` `Start-UdpRelay` runspaces never disposed → leak in long-running NSSM service — track the runspace and dispose it in shutdown (matches the control-runspace path).
- [x] (valid, Copilot) `.ps1` relay loop treats every `SocketException` as a timeout, swallowing real socket errors — discriminate `SocketErrorCode == TimedOut` (heartbeat) and log any other. The `.py` already catches `socket.timeout` narrowly, so this was a `.ps1`-only gap.

### False positives
- (none)

## Integrated Review
**Status**: complete
**When**: 2026-06-10 12:05 -04:00
**By**: Claude Code Agent (Claude Opus 4.8, 1M context)

**PR**: #29 at `7b9ba8b`
**Sources**: 1 (Copilot R1 @ `42a5909`, pre-fix) + prior Integrated Review @ `42a5909`
**Cross-source confirmations**: 0 new
**CI**: all-pass (build-and-test SUCCESS @ `7b9ba8b`)

Re-triage after pushing the round-1 fixes. No fresh Copilot round has landed at
the current head; this round verifies the R1 findings against the fixed code.

### Findings
- (addressed) Copilot R1 #1 idle "0 pkts" log — `logged = 0` confirmed at `proxy.py:177`.
- (addressed) Copilot R1 #2 aux-runspace leak — `$auxRelays {Ps,Rs}` + `$r.Rs.Dispose()` confirmed at `proxy.ps1:351`.
- (addressed) Copilot R1 #3 SocketException swallow — `SocketErrorCode == TimedOut` discrimination confirmed at `proxy.ps1:163`.

No open items. Holding merge until a fresh Copilot round at `7b9ba8b` is clean
(per the wait-for-Copilot rule); the re-review must be re-requested in the web UI.

### False positives
- (none)
