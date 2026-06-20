---
issue: 60
---

# Issue #60 — fix(garmin_sidescan): decode GCV-10 water-column (per-channel extractor)

## Local Review
**Status**: complete
**When**: 2026-06-20 19:07 -04:00
**By**: Claude Code Agent (Claude Opus 4.8 (1M context))
**Verdict**: changes-requested

**PR**: #61 at `45f1d70`
**Mode**: post-PR
**Depth**: Standard (reason: decode-correctness path in sensor driver; 146/-51 lines, single package)
**Must-fix**: 1 | **Suggestions**: 3

### Findings
- [ ] (must-fix) `_publish_diagnostics` still reads removed `self._bytes_per_sample` → AttributeError on the 1.0s diagnostics timer (first tick ~1s after start, every deployment), tearing down the sonar-liveness diagnostic — `garmin_sidescan/garmin_sidescan/node.py:851`
- [ ] (suggestion) `dtype_bits` diagnostic key is semantically stale even once the crash is fixed: width is per-channel now (GCV-10 emits 8-bit side-scan + 16-bit water-column), so a single scalar can't describe the device — drop it or make it per-channel — `garmin_sidescan/garmin_sidescan/node.py:851`
- [ ] (suggestion) Test coverage gap: test fakes still set the removed `_bytes_per_sample`/`_sonar_dtype` attrs and pass no `bits` (green only via the `bits=16` default); no `_publish_diagnostics` smoke test (why the suite missed the must-fix) and no `bits=8` `_make_sonar_msg` assertion — `garmin_sidescan/test/test_safety.py:426,455-456`
- [ ] (suggestion) Per-run extractor stability is assumed, not enforced: extractor is re-derived per packet but `_cur_bits`/leading-strip are pinned on the run's first packet. A garbled/non-sample first packet (no FH/FHS → classified echo/16-bit) followed by genuine dark packets would mix 8-bit bytes under bits=16 → wrong dtype+scale. Cannot trigger on validated captures; robust fix is to pin the extractor for the run on channel-change — `garmin_sidescan/garmin_sidescan/decode.py:579-583`
