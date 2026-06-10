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
