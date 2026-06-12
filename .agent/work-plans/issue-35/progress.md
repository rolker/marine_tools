---
issue: 35
---

# Issue #35 — garmin_sidescan: publish per-channel range scale from sub-header v2 (sample_rate currently 0 or wrong under auto-range)

## Local Review (Pre-Push)
**Status**: complete
**When**: 2026-06-11 21:12 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: approved (findings addressed in 1c91f34)

**Branch**: feature/issue-35 at `1c91f34`
**Mode**: pre-push
**Depth**: Standard (reason: ~600 lines / 11 files, sensor-driver publish path)
**Must-fix**: 1 | **Suggestions**: 7 (1 Copilot false positive dropped)

### Findings
- [x] (must-fix) replay tool: --rate <= 0 unvalidated divisor — `tools/replay_debug_raw.py:85` (cross-model: Claude + Copilot)
- [x] (suggestion) down-look must not take commanded-range fallback (~2x wrong scale) — `garmin_sidescan/node.py` (cross-confirmed: adversarial + governance)
- [x] (suggestion) nadir max_range should be the ping's own v2, not the side-scan command clamp — `garmin_sidescan/node.py`
- [x] (suggestion) verify tool: filter RawSonarImage topics by name (M3 pollution) — `tools/verify_range_scale.py`
- [x] (suggestion) verify tool: anchor window to first raw datagram (portability) — `tools/verify_range_scale.py`
- [x] (suggestion) verify tool: missing assembler flush — `tools/verify_range_scale.py`
- [x] (suggestion) tools/README: best-effort QoS flag on topic-echo example — `tools/README.md`
- [x] (governance) platform follow-up for _nadir URDF frame + nadir_depth recording + freq params — filed rolker/unh_echoboats_project11#254
- Copilot claim that replay t0 anchors to any topic: false positive (loop continues on non-raw topics before t0 is set)

Verification: 64/64 tests; ament flake8/pep257 clean; end-to-end UDP replay of the 2026-06-10 Cod Rock bag through the real node — consumer recovered port/stbd {47.8, 30.4} m across the auto-range step, down {10.8, 11.2} m, nadir_depth 7.62–7.76 m on the shoal.

## Integrated Review
**Status**: complete
**When**: 2026-06-12 00:18 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))

**PR**: #36 at `4d7ebfe` (fixes landed in `4a060d8`)
**Sources**: 4 (Copilot R1 @ `655f779`, R2 @ `c1b72cb`, R3 @ `4d7ebfe`, Local Review (Pre-Push) timeline)
**Cross-source confirmations**: 1
**CI**: all-pass (build-and-test, copilot-reviewer)

### Findings
- [x] (cross-confirmed: Copilot R1+R2+R3; consequence of Local-Review remediation) build_nadir_range docstring said max_range = "configured swath maximum" but caller passes the ping's own v2 — `garmin_sidescan/node.py:92`
- [x] (valid, Copilot R2+R3) nadir_depth could publish spec-invalid Range (range > max_range) on corrupt-but-parseable v1; now gated 0 < v1 <= v2 + test — `garmin_sidescan/node.py:818`
- [x] (valid, Copilot R1 x2) verify tool xlabel + --start help said "first bag message"; anchor is first raw datagram — `tools/verify_range_scale.py`
- [x] (valid, Copilot R2) tools hard-coded echo_layer so GCV-10 bags rendered nothing; now generation-voted like the driver (waterfall had the same unflagged flaw, fixed too) — `tools/verify_range_scale.py`, `tools/sidescan_waterfall.py`
- [x] (valid, Copilot R3) --source help typo — `tools/sidescan_waterfall.py`

### False positives
- none — all five distinct findings were real

## Integrated Review
**Status**: complete
**When**: 2026-06-12 00:38 -0400
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))

**PR**: #36 at `5c03d4b` (Copilot round 4; fixes in this commit)
**Sources**: 2 (Copilot R4 @ `5c03d4b`, prior Integrated Review timeline)
**Cross-source confirmations**: 0
**CI**: all-pass

### Findings
- [x] (valid, Copilot R4) parse_subheader lacked field3==0 / field6-follows invariant checks; corrupt-but-parseable header could yield bogus scale — `garmin_sidescan/decode.py`
- [x] (valid, Copilot R4) verify tool picked RadarControlSet topic by type only; now namespace-matched to the driver's ~/state — `tools/verify_range_scale.py`
- [x] (valid, Copilot R4) O(N*M) nadir attach -> searchsorted — `tools/sidescan_waterfall.py`

### False positives
- (Copilot R4, x4) re-raised rounds-1-3 comments verbatim (xlabel/--start wording, hard-coded extractor, --source typo) — all fixed in 4a060d8 and present at the reviewed head; cited line numbers point at text that no longer exists
