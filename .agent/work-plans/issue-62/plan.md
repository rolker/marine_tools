# Plan: garmin_sidescan — populate `ping_info` frequency + beamwidth from generation lookup table

## Issue

https://github.com/rolker/marine_tools/issues/62

## Context

The `garmin_sidescan` driver already detects device generation (`gcv10`/`gcv20`) via
`_detect_generation()` / `generation_from_layers()`, and stores it in `self._detected_gen`.
At construction the driver builds `self._freq` from three explicit `freq_*_hz` parameters,
all defaulting to `0.0` (= "unavailable" per `RawSonarImage`/`PingInfo` convention).
`_make_sonar_msg()` stamps `msg.ping_info.frequency = self._freq[side]` and leaves
`msg.ping_info.rx_beamwidths` empty — so both fields are zero/absent even for known
generations where the constants are well-characterised.

This issue adds a `(generation, channel-role) → Hz` frequency table and a
`(generation, channel-role) → float` beamwidth table (across-track half-angle in radians)
so that `ping_info.frequency` and `ping_info.rx_beamwidths` are populated automatically
once the generation is latched, with explicit parameters still winning (param != 0.0
overrides the table value).

### Source reading summary

- `node.py:259–263` — `self._freq` dict built at `__init__` from params; 0.0 = unavailable.
- `node.py:789` — `msg.ping_info.frequency = self._freq[side]`; no `rx_beamwidths` set.
- `node.py:659–671` — `self._assembler` built lazily on first latched generation; the latched
  gen is available as `self._detected_gen` or falls through to the explicit `device` param.
- `decode.py:203–223` — `generation_from_layers()` returns `'gcv10'`/`'gcv20'`; called per
  packet to vote until `GEN_VOTE_MIN (5)` packets agree. After that, `self._detected_gen` is
  stable for the session.
- `rx_angles`/`tx_angles` remain `[0.0]` (orientation via TF — issue #15, settled).
- `ping_info.sound_speed` remains 1500 nominal (settled).

### Frequency values (GT34UHD-TM on GCV-20; GCV-10 per Garmin spec sheets)

| Generation | Role   | Band (kHz)    | Band-center used (Hz) | Note |
|-----------|--------|---------------|-----------------------|------|
| gcv20     | port   | 1,060–1,170   | 1,120,000             | "1,200 kHz" is rounded marketing label |
| gcv20     | stbd   | 1,060–1,170   | 1,120,000             | same transducer, symmetric |
| gcv20     | down   | 760–880       | 820,000               | ClearVü |
| gcv10     | port   | ~455 kHz nom  | 455,000               | SideVü |
| gcv10     | stbd   | ~455 kHz nom  | 455,000               | SideVü |
| gcv10     | down   | ~800 kHz nom  | 800,000               | ClearVü |

### Beamwidth values (GT34UHD-TM) — full −3 dB widths, radians (PingInfo.msg is authoritative)

**Convention (operator-settled 2026-06-21):** a sidescan does no across-track
beamforming, so the across-track *receive* beam's −3 dB directivity **is** the wide fan.
Carry both axes across the two fields:
- `rx_beamwidths = [across-track full −3 dB beamwidth, rad]` — the wide receive fan.
- `tx_beamwidths = [along-track full −3 dB beamwidth, rad]` — the narrow resolution dim.

Both are the **full** −3 dB width in **radians** — NOT a half-angle. (The earlier
half-angle / `rviz_sonar_image ±rx_beamwidths` framing was wrong; that half-vs-full read
and CUBE's degrees-vs-radians read (cube#30) are **consumer bugs**, fixed separately — the
producer follows the .msg.)

| Generation | Role | Along-track → `tx_beamwidths` | Across-track → `rx_beamwidths` |
|-----------|------|-------------------------------|--------------------------------|
| gcv20 | port/stbd (SideVü) | 0.44° → 0.00768 rad | 55° → 0.9599 rad |
| gcv20 | down (ClearVü)      | 0.74° → 0.01292 rad | 46° → 0.8029 rad |
| gcv10 | — | not populated (spec unconfirmed) | not populated |

## Approach

### Step 1 — Add lookup tables to `node.py`

Add two module-level dicts near the top of `node.py` (after the `FRAME_SUFFIX` dict):

```python
# (generation, side) → centre frequency in Hz.
# GCV-20 SideVü band is 1,060–1,170 kHz; "1,200 kHz" is a rounded marketing label —
# use the band centre 1,120 kHz.  ClearVü (down) band is 760–880 kHz, centre 820 kHz.
# GCV-10 values are nominal Garmin spec-sheet figures (455 / 800 kHz).
_FREQ_HZ = {
    ('gcv20', 'port'):  1_120_000.0,
    ('gcv20', 'stbd'):  1_120_000.0,
    ('gcv20', 'down'):    820_000.0,
    ('gcv10', 'port'):    455_000.0,
    ('gcv10', 'stbd'):    455_000.0,
    ('gcv10', 'down'):    800_000.0,
}

# (generation, side) → full −3 dB beamwidth in radians (PingInfo.msg = radians).
# Sidescan: rx = across-track (the wide receive fan / directivity); tx = along-track
# (the narrow resolution dim).  FULL −3 dB widths, not half-angles.  GCV-10 unconfirmed.
_RX_BEAMWIDTH_RAD = {                          # across-track (wide fan)
    ('gcv20', 'port'): math.radians(55.0),     # SideVü
    ('gcv20', 'stbd'): math.radians(55.0),
    ('gcv20', 'down'): math.radians(46.0),     # ClearVü
}
_TX_BEAMWIDTH_RAD = {                          # along-track (narrow resolution)
    ('gcv20', 'port'): math.radians(0.44),     # SideVü
    ('gcv20', 'stbd'): math.radians(0.44),
    ('gcv20', 'down'): math.radians(0.74),     # ClearVü
}
```

### Step 2 — Apply tables in `_make_sonar_msg()`

After the generation is latched, `self._detected_gen` holds `'gcv20'` or `'gcv10'` (or
`None` briefly before auto-detect converges; the explicit `device` param overrides it at
assembler-build time, so use `self._device if self._device in ('gcv20','gcv10') else self._detected_gen`
to get the effective generation). Refactor `_make_sonar_msg()` to:

1. Compute effective generation at call time (cheap dict lookup):

```python
gen = self._device if self._device in ('gcv20', 'gcv10') else self._detected_gen
```

2. Frequency — use explicit param if non-zero, else table:

```python
freq = self._freq[side]                      # explicit param (set at __init__)
if freq == 0.0 and gen is not None:
    freq = _FREQ_HZ.get((gen, side), 0.0)
msg.ping_info.frequency = freq
```

3. Beamwidth — populate `rx_beamwidths` (across-track wide fan) **and** `tx_beamwidths`
   (along-track narrow) from the tables once the generation is known (full −3 dB, radians):

```python
if gen is not None:
    rx = _RX_BEAMWIDTH_RAD.get((gen, side))
    tx = _TX_BEAMWIDTH_RAD.get((gen, side))
    if rx is not None:
        msg.ping_info.rx_beamwidths = [rx]   # across-track (wide receive fan)
    if tx is not None:
        msg.ping_info.tx_beamwidths = [tx]   # along-track (narrow resolution)
# Both are full −3 dB widths in radians (PingInfo.msg). CUBE reads rx_beamwidths as
# degrees (cube#30) and rviz_sonar_image as a half-angle — consumer bugs, fixed
# separately, not reasons to bend the producer.
```

Note: `rx_beamwidths` is NOT set if the generation is unknown (pre-detect warmup) — this
keeps the "0 = unavailable" convention instead of stamping a wrong value.

### Step 3 — Update README

Add a "Sensor constants" sub-section to `README.md` documenting:
- The axis convention for `rx_beamwidths` (across-track half-angle, matching
  `rviz_sonar_image` / `cube_bathymetry`).
- The frequency table with the "1,200 kHz marketing label vs. 1,120 kHz band-centre" note.
- The recommendation to propose an upstream `PingInfo.msg` comment clarifying
  `rx_beamwidths` for single-beam/sidescan (not done here — out of scope).
- GCV-10 beamwidths deliberately not populated (spec not confirmed).

### Step 4 — Tests

Add to `test/test_node.py` (create the file if it does not yet exist):

1. **`test_freq_beamwidth_table_coverage()`** — unit test on the module-level dicts:
   - Assert all six `_FREQ_HZ` entries are present and positive.
   - Assert all three `_BEAMWIDTH_RAD` entries are present, positive, and < π/2.
   - Assert GCV-20 SideVü entry is close to `math.radians(27.5)`.

2. **`test_make_sonar_msg_uses_table_when_param_zero()`** — integration test:
   Build a minimal `_make_sonar_msg`-equivalent (or extract the logic into a
   testable helper) that, given `self._freq[side] == 0.0` and `gen == 'gcv20'`,
   returns a message with `ping_info.frequency == 1_120_000.0` and
   `ping_info.rx_beamwidths == [math.radians(27.5)]` for side `'port'`.
   Repeat for `'down'` channel.

3. **`test_explicit_param_overrides_table()`** — same setup but with
   `self._freq['port'] == 500_000.0`; assert `msg.ping_info.frequency == 500_000.0`
   (explicit param wins).

4. **`test_unknown_generation_leaves_zero_frequency_and_no_beamwidth()`** — when
   `gen is None` (auto-detect not yet converged), assert `msg.ping_info.frequency == 0.0`
   and `msg.ping_info.rx_beamwidths` is empty.

Since `_make_sonar_msg()` calls several ROS message constructors (requires `rclpy`),
the cleanest approach is to **extract the frequency/beamwidth resolution logic** into a
pure-Python helper function `_resolve_freq_bw(gen, side, freq_override)` → `(freq, bw_or_none)`
that has no ROS dependency and can be tested without a node context. The `_make_sonar_msg`
method calls this helper. This keeps tests dependency-free (no ROS spin required).

## Files to Change

| File | Change |
|------|--------|
| `garmin_sidescan/node.py` | Add `_FREQ_HZ` + `_BEAMWIDTH_RAD` module-level dicts; add `_resolve_freq_bw()` helper; update `_make_sonar_msg()` to call helper |
| `garmin_sidescan/node.py` | Update `__init__` docstring/comment for `freq_*_hz` params to mention table fallback |
| `README.md` | Add "Sensor constants" section: axis convention, frequency table note, GCV-10 caveat, upstream recommendation |
| `test/test_node.py` | New file: four unit tests covering the lookup table, table-fills-zero, param-overrides-table, and no-gen cases |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| A change includes its consequences | README updated; tests for every new code path; no silent empty `rx_beamwidths` for known generations |
| Only what's needed | No new params, no new topics; only the two fields that the issue explicitly scopes. GCV-10 beamwidths deliberately omitted (spec unconfirmed). |
| Test what breaks | Tests cover the table lookup, the param-override path, and the pre-detect `None` path — these are the three regressions that could silently wrong-configure a downstream consumer |
| Capture decisions, not just implementations | README documents the axis convention and the marketing-label quirk; the "1,200 kHz" comment is in the code and the README |
| Improve incrementally | Single PR, minimal surface; no attitude-aware projection, no URDF, no roll correction (Stage 2–4 per #185) |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| ADR-0002 (worktree isolation) | Yes | Working in `feature/issue-62` worktree |
| ADR-0001 (ADRs for design decisions) | Marginal | No new ADR needed — the axis convention is documented in README and code comment; the decision follows existing precedent (`rviz_sonar_image` consumer convention) |
| ADR-0009 (Python package management) | No | No new dependencies; `math` is stdlib |

## Consequences

| If we change… | Also update… | Included in plan? |
|---|---|---|
| `ping_info.frequency` populated for known gen | Any consumer that checked for `0.0` as "unknown" will now see a real value — this is intended by the spec | Yes — no action needed |
| `ping_info.rx_beamwidths` populated for GCV-20 | `rviz_sonar_image` and `cube_bathymetry` will now see a non-empty beamwidth and render a wedge — desired behaviour | Yes — documented in README |
| `_make_sonar_msg()` signature unchanged | Callers (`_emit_ping`) unchanged | Yes — internal refactor only |
| `_FREQ_HZ` / `_BEAMWIDTH_RAD` are module-level | test imports can access them directly without a ROS node | Yes — step 4 tests import from the module |
| README gains "Sensor constants" section | Existing freq_*_hz parameter table row updated to mention table fallback | Yes — step 3 |

## Open Questions

- [ ] No open questions — plan is review-plan-ready.

## Estimated Scope

Single PR. Four-step change: two new dicts + helper in `node.py`, one README section, one new test file (~80 lines total).
