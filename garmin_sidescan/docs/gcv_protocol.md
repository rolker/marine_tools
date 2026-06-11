# Garmin GCV Marine-Network protocol reference

Every message the `garmin_sidescan` driver sees or sends on the Garmin Marine
Network (GCV-10 / GCV-20), in one place. The [summary](#1-summary) is the
at-a-glance map; [patterns](#2-cross-message-patterns) calls out the structure
shared across messages; the [per-message detail](#3-per-message-detail) sections
give byte layouts.

The foundational reverse engineering of the GCV Marine-Network protocol — the
imagery stream (`eb07`/`d807` render-layer model) and the TCP command frames
(`d207efbe`: transmit/range/TVG/interference) — is the work of **Dan
Tauriello**, validated live on both the GCV-10 and GCV-20. The driver's
`decode.py` and `commands.py` implement his findings. The status (`8e03`) and
config (`e508`/`e708`) decodes and the cross-message structural analysis in this
document build on that base, from the 2026-06-10 capture.

Every claim is grounded in a capture or in the driver source, not assumption.
Provenance and anything still unverified are called out explicitly.

- **Imagery + command frames** — reverse-engineered by **Dan Tauriello**;
  authoritatively implemented and unit-tested in `garmin_sidescan/decode.py`
  (imagery) and `garmin_sidescan/commands.py` (TCP control, verified live on
  both generations 2026-06-05). This reference summarizes and cross-links them
  rather than restating them. Command frames are transmitted, not captured.
- **Status + config decode (this document)** — from the wet capture below.
- **Wet capture:** `bag_2026-06-10T15.54.41_sidescan_raw` (gabby), 2026-06-10
  Piscataqua River deployment (rolker/unh_echoboats_project11#250):
  `debug/raw` (227,777 imagery datagrams), `debug/raw_status` (215),
  `debug/raw_config` (1,497), spanning two Cod Rock shoal passes.
- **Ground truth (depth/bathymetry):** the M3 multibeam in the same sonar bag
  (same clock) — used to test the `0xe4` value (§4) and the bin-size ladder.

---

## 1. Summary

All multicast streams are listen-only; the TCP command path is the only thing
the driver sends. "LE id" is the 2-byte magic read as a little-endian `uint16`
(see [pattern B](#b-the-magic-is-a-little-endian-message-type-id)).

| Message | Magic | LE id | Transport | Dir | Len | ~Rate | Purpose | Decode |
|---------|-------|------:|-----------|:---:|-----|------:|---------|--------|
| Imagery data | `eb 07` | 2027 | `239.254.2.1:50220` UDP | in | 650–956 B | ~190/s | side/down scan-line sample packets | ✅ `decode.py` |
| Channel marker | `d8 07` | 2008 | `239.254.2.1:50220` UDP | in | 15–16 B | per run | delimits one channel's packet run within a ping | ✅ |
| Keepalive | `d1 07` | 2001 | `239.254.2.1:50220` UDP | in | 22 B | ~1 Hz | chartplotter-master keepalive that sustains pinging | ⚠️ partial |
| Status | `8e 03` | 910 | `239.254.2.2:50050` UDP | in | 34 B | ~0.2/s | tx flag + a held `0xe4` sub-type value (NOT depth — §4) / settings echo | ◐ this doc |
| Config heartbeat | `e7 08` | 2279 | `239.254.2.11:51000` UDP | in | 19 B | ~1/s | device-id heartbeat | ⚠️ partial |
| Config record | `e5 08` | 2277 | `239.254.2.11:51000` UDP | in | 168 / 173 B | bursts | named CDP ping-schedule key/values | ◐ structure ✅, values partial |
| Command | `d2 07 ef be` | 2002 | `172.16.3.0:50227` TCP | **out** | varies | on demand | transmit / range / TVG / interference | ✅ `commands.py` |

Decode legend: ✅ understood · ◐ partial · ⚠️ envelope only.

**Three logical planes:**

- **Imagery plane** — `239.254.2.1:50220`: `eb07` data, `d807` markers, `d107`
  keepalive. The data/control plane (`0x07xx` ids).
- **Status plane** — `239.254.2.2:50050`: `8e03` only.
- **Config plane** — `239.254.2.11:51000`: `e708` heartbeat + `e508` records
  (`0x08xx` ids).
- **Command plane (outbound)** — TCP `:50227`: `d2 07 ef be` frames. The driver
  uses this to control the sonar directly, sidestepping the chartplotter's CDP.

---

## 2. Cross-message patterns

The whole protocol is built from a few repeated shapes — this is the part worth
scanning for structure.

### A. One universal envelope

Every **inbound** frame is:

```
<2-byte magic>  00 00  <uint32 LE payload-length>  <payload>
└── offset 0 ──┘└ 2-3 ┘└──── offset 4..7 ───────┘ └ 8.. ┘
```

Verified to hold on **100 %** of captured frames of every type
(`payload-length == total_len − 8` for all eb07/d807/d107/8e03/e708/e508). The
`00 00` after the magic is always zero — likely a 16-bit high half of a 32-bit
message id, or a reserved field.

The **outbound TCP command** is the one variant: a **4-byte** magic
`d2 07 ef be` + `uint32 LE length` + payload, with no `00 00` pad. Note it still
opens `d2 07` — the same `0x07xx` family as the imagery plane.

### B. The magic is a little-endian message-type id

Read each magic as LE `uint16` and a numbering appears:

| id | hex | message | plane |
|---:|-----|---------|-------|
| 2001 | `0x07d1` | keepalive | imagery |
| 2002 | `0x07d2` | command (`d207…`) | command |
| 2008 | `0x07d8` | channel marker | imagery |
| 2027 | `0x07eb` | imagery data | imagery |
| 2277 | `0x08e5` | config record | config |
| 2279 | `0x08e7` | config heartbeat | config |
| 910 | `0x038e` | status | status |

The high byte groups the plane: `0x07xx` = imagery/command, `0x08xx` = config.
Status (`0x038e`) sits on its own. Useful as a sanity filter when sniffing raw
traffic: anything `?? 07` / `?? 08` / `8e 03` is GCV.

### C. A recurring node descriptor: `<n> 01 03 0d <5-byte node id>`

The config and keepalive payloads open with the same shape:

```
e7 08 (heartbeat) payload:  03 01 03 0d  90 db a2 88 0b  11 01
e5 08 (record)    payload:  05 01 03 0d  90 db a2 88 0b  1f 15 …key…
d1 07 (keepalive) payload:  04 01 03 0d  d5 a7 f2 8b 0d  12 8a 18 19 03
                            ▲  └ tag ┘  └─ node id ──┘
                            leading count?
```

- `01 03 0d` is a fixed descriptor tag.
- The **5-byte node id** differs by source — `90 db a2 88 0b` on the config
  plane vs `d5 a7 f2 8b 0d` on the keepalive — i.e. **two devices** announcing
  on the bus (GCV vs chartplotter master). The trailing `0b` / `0d` and the
  4-byte body look MAC/serial-derived.
- The leading byte (`03` / `04` / `05`) looks like a field/record count.

### D. A ping-link token ties markers to data

The `d807` channel marker embeds the same token that opens the `eb07`
sub-header it delimits:

```
eb 07 …envelope… 0e 01 03 09 00  13 ea ef 01 19 00  23 ae eb …samples…
d8 07 …envelope…              02  13 ea ef 01 19 00
                                  └── shared ping token ──┘
```

So a marker is not a bare delimiter — it carries the ping/sequence id of the run
it closes.

### E. CDP named TLV + LEB128 values

Config records (`e508`) are ASCII-keyed type-length-value:

```
<tag> <len> <key-ascii> 00 <value-record> …   (repeated)   2f 08 <LEB128 timestamp>
```

Keys seen: `yutl-port/stbd/cntr-engn-sched`, `yutl-engn-sched-type`,
`stnd-sched`, `opt-sched-1/2/3`. Records close with a `2f 08`-tagged **LEB128
varint timestamp**. The same unsigned base-128 varint encodes the **range value
(0.5 mm units)** in the TCP command — so LEB128 is the protocol's number format.

---

## 3. Per-message detail

### 3.1 Imagery data — `eb07` (`239.254.2.1:50220`)

Side-scan and down-look sample packets; a scan line is reassembled from a run of
same-channel packets bracketed by `d807` markers. The render-layer model
(per-generation echo extraction, GCV-10 "dark layer" vs GCV-20 16-bit first
layer, trailer/leading-header stripping) was reverse-engineered by **Dan Tauriello**
and is fully documented and unit-tested in **`decode.py`** — refer there for the
authoritative layout. Key sub-header bytes
(full-frame offsets, after the 8-byte envelope):

| offset | meaning |
|-------:|---------|
| 8 | render-layer / beam-type byte: `0x0d` down-look (water column), `0x0e`/`0x0f` side-scan |
| 12 | channel number (GCV-20 map: 0 = port, 1 = stbd, 2 = down) |
| 13 | **range/scale index** — the coarse display bracket; steps with range (`0x11`/`0x12`/`0x13` seen), shared by all channels. NOT a generation tag (see below). |
| 14… | **LEB128 varint** — on the down-look, the per-ping **measured bottom range** (~0.5 mm units, see below). Variable length (2–3 B), then a `19 00 23` marker. (This is the "token" the `d807` marker echoes — [pattern D](#d-a-ping-link-token-ties-markers-to-data) — not a sequence counter.) |

Frequency and a per-ping timestamp are **not** carried; the driver takes
frequency from a parameter and stamps scan lines with receive time (see
`decode.py` / README).

#### Distinguishing the beams (down / port / starboard)

Diffing the three channels' sub-headers within one range/time window
(2026-06-10, byte 13 = `0x13`):

| offset | port (ch0) | stbd (ch1) | down (ch2) | note |
|-------:|:----------:|:----------:|:----------:|------|
| 8 | `0e` | `0e` | **`0d`** | beam type — separates **down vs side-scan** only |
| 9–11 | `01 03 09` | `01 03 09` | `01 03 09` | constant |
| **12** | **`00`** | **`01`** | **`02`** | **channel number** |
| 13 | `13` | `13` | `13` | range index (same on all channels) |
| 14… | varint + `19 00 23` | varint + … | varint + … | per-ping range varint (down-look = bottom range); whether the side-scan value is meaningful is open |
| (after) | `be 93 06` | `be 93 06` | `c4 fa 02` | further sub-header / start of render data |

So:

- **Down vs side-scan** is intrinsic at **offset 8** (`0x0d` down vs
  `0x0e`/`0x0f` side). (The `0x0e`/`0x0f` split is generation, not side.)
- **Port vs starboard:** in this capture the two side-scan channels are
  byte-for-byte identical in the sub-header **except the channel number at
  offset 12** — no other byte separates them here. The driver therefore maps
  side from the channel number via the `port_channels` / `stbd_channels` params.
  Whether an intrinsic side marker exists elsewhere (other offsets not examined,
  the render payload, or a different capture) is **not yet established**; note
  only that the channel→side mapping itself differs by generation (GCV-20
  `0/1/2` vs the GCV-10 survey `3/1`).

#### Byte 13 is a range/scale index, not a generation tag

`decode.py` historically read offset 13 as a GCV-10-vs-GCV-20 "generation tag"
(`0x11`→gcv10, `0x12`→gcv20). **The 2026-06-10 data disproves this** — that
assertion was an over-eager guess, not a verified fact:

- On a single GCV-20, byte 13 takes **`0x11`, `0x12`, and `0x13`** purely as a
  function of range — identical on all three channels, stepping at the auto-range
  transitions (`0x13` ≈ 19 m display / `0x12` ≈ 14 m / `0x11` shallowest,
  recovery-only).
- The **GCV-10** survey fixture reads byte 13 = **`0x13`** — the *same* value a
  GCV-20 produces in deep water. A value shared across generations cannot be a
  generation tag.

So byte 13 is the **coarse range bracket** (higher = longer range), one of two
range controls — the other is the per-ping bottom-range varint below. It is an
index, so converting it to metres needs a calibrated ladder (only `0x11`–`0x13`
seen so far; a TCP range-sweep would map the rest).

Why `decode.py`'s old detection still "worked": its default extractor is GCV-10,
and its one live rule (`0x12` → switch to GCV-20) happens to fire for the common
GCV-20 range. But a GCV-20 in deep water (`0x13`, unmapped) falls back to the
**GCV-10 extractor** until a `0x12` ping arrives — wrong imagery unless the launch
pins `device:=gcv20`. Tracked as a `marine_tools` decode bug.

#### Generation is recoverable from packet structure (range-independent)

The real GCV-10/GCV-20 difference is **structural**, in the render layers — and
it is independent of range:

| generation | render layers | later-layer (`SH`/`SHS`) headers | echo | samples |
|------------|---------------|----------------------------------|------|---------|
| **GCV-10** | 3 | **2** | last ("dark") layer | 8-bit (`UINT8`) |
| **GCV-20** | ≤2 | **0 or 1** | first (`FH`) layer | 16-bit LE (`UINT16`) |

Verified: every GCV-10 fixture packet has two later-layer headers `(1,2)`; every
GCV-20 packet (fixture *and* the 06-10 bag, at **both** byte-13 ranges) has at
most one `(1,0)`/`(1,1)`. So a packet can be classified **per-packet by counting
its `SH`/`SHS` headers (≥2 → GCV-10, ≤1 → GCV-20)** — no external generation
knowledge, and immune to the range-coupling that makes byte 13 unusable for this.
(GCV-10 evidence is one 16-packet fixture; widen before relying on it.)

#### Range has two controls: the byte-13 bracket + the per-ping bottom-range varint

The full **down-look sub-header** is three LEB128 varints (all in **0.5 mm
units** — the TCP range command's unit) bracketed by fixed markers. Structure
verified byte-for-byte on **29,267 packets, 0 mismatches** (`parse_downlook_subheader`):

```
08: 0d 01 03 09   beam (0d=down) + const
12: <channel>
13: <bracket>     coarse range index (byte 13, above)
14: v1  ───────── BOTTOM RANGE   (depth; 7.1–19.9 m; corr 1.00 / ratio 1.013 vs M3)
    19 00 23      marker
    v2  ───────── DISPLAY RANGE  (scan extent; 10.8–24.2 m; corr 0.99)
    2a            marker
    v3  ───────── ~88–99 mm      (near-field / start range?; corr 0.94; meaning TBD)
    31 02 3f      const
    da 04 d8 04   FH header → samples begin
```

- **v1 = measured bottom depth**, per ping, and **shared across all channels**
  (the boat's depth; near-exact vs M3 — *not* the held/laggy `0xe4` value).
- **v2 = this channel's display range** — the auto-ranged scan extent, and it is
  **per channel**: on the down-look it is the water-column depth range (e.g.
  24.2 m, with `v1/v2 ≈ 0.79` so the bottom sits ~79 % down); on the side-scan it
  is the across-track slant range (~50 m, ~2× the water column). So
  **`bin_size = v2 / n_bins`** must use the matching channel's v2 (no bottom
  detection, no ladder, no M3 — `tools/sidescan_waterfall.py` uses exactly this).
  The same sub-header layout (and the structural markers) is present on every
  channel; only the channel/layer bytes and v2/v3 differ.
- **v3 ≈ 96 mm**, weakly depth-correlated — a candidate near-field/blanking or
  start-range term (possibly the residual sonar-vs-M3 offset).

byte 13 is the coarse bracket that gates which range band v2 lives in; v2 is the
actual per-ping range. `n_bins` (~2034) is **not** a range control — its short
values (848/1148/1500/1748 ≈ 2034 − N×309) are dropped-packet assembly artifacts.
(Verify with `.agent/scratchpad/gcv_re.py varint`.)

### 3.2 Channel marker — `d807` (`239.254.2.1:50220`)

15–16 byte delimiter emitted between channel runs. Payload
`02 <ping-token>` where the ping token (`13 ea ef 01 19 00`) matches the
following `eb07` sub-header ([pattern D](#d-a-ping-link-token-ties-markers-to-data)).
The assembler treats it as "flush the current channel's accumulation."

### 3.3 Keepalive — `d107` (`239.254.2.1:50220`)

22-byte frame, ~1 Hz, from the chartplotter master. Sustains pinging (the GCV
will not run without the master's hardware enable + keepalive). Payload
`04 01 03 0d <chartplotter node id> 12 8a 18 19 03` — the node-descriptor shape
of [pattern C](#c-a-recurring-node-descriptor-n-01-03-0d-5-byte-node-id). Body
after the id (`12 8a 18 19 03`) is not decoded; not needed (the driver does not
synthesize keepalives — it relies on the real chartplotter).

### 3.4 Status — `8e03` (`239.254.2.2:50050`) — tx flag + an undeciphered sub-type

Fixed 34 bytes. The stream interleaves **two sub-types**, discriminated by the
payload byte at **offset 9** (`0x00` or `0xe4` in the capture; the two are
emitted concurrently, not tied to a phase). Shared frame:

| offset | bytes | meaning |
|-------:|-------|---------|
| 0–3 | `8e 03 00 00` | magic + pad |
| 4–7 | `1a 00 00 00` | LE length (26) |
| 8 | `02` | constant |
| **9** | `00` \| `e4` | **sub-type discriminator** |
| 10–16 | `0a 0c 00 00 03 01 00` | constant |
| 17–23 | *sub-type specific* | `0xe4` value (below) or settings echo |
| 24–29 | `e0 a0 91 0b 01 04` | constant device/message tail |
| 30–31 | u16 LE | monotonic counter (uptime/sequence, ~1/frame) |
| 32–33 | `00 00` | constant |

Constant region confirmed byte-for-byte across all 124 `0x00` + 91 `0xe4`
frames; only offsets 17–23, the 30–31 counter, and byte 9 vary.

**Sub-type `0xe4` — a held value of unconfirmed meaning (NOT depth):**

```
offset 17 18 19 | 20 21 | 22 23
        00 00 00 | VV VV | 00 00      value = u16_LE(20)  (offsets 17-19/22-23 = 00)
```

The offset-20 `u16` is **not a depth**: it is **held** (one value for tens of
seconds), lags the M3 nadir by 2–7 m, and does not track the bottom (it only
*looked* depth-like because its mean and Cod-Rock dips happened to align). Its
meaning is unconfirmed — likely a mode/status field. The driver does **not**
decode or publish it. A real per-ping bottom range is instead in the **down-look
imagery sub-header** (§3.1, offset-14 varint); for a nadir *depth*, bottom-track
the down-look in a downstream node (issue #16).

**Sub-type `0x00` — settings echo (NOT the active range):**

Offsets 17–23 = `ae 05 c0 75 ae 05 c0`, **constant** the entire capture
including both Cod Rock auto-range transitions. This is the **stale
commanded/configured** value (it matches the driver's `state` holding
`range: 60.0` while the hardware auto-ranged underneath). Exact field decode of
the repeated `ae 05 c0` is unconfirmed and not needed.

### 3.5 Config — `e708` heartbeat + `e508` records (`239.254.2.11:51000`)

The chartplotter's CDP config broadcast (it owns range/freq/schedule on the
bus). Two frames:

- **`e708` (19 B, ~1 Hz)** — device-id heartbeat:
  `03 01 03 0d 90 db a2 88 0b 11 01` (node descriptor + `11 01`). Static.
- **`e508` (168 / 173 B, in bursts)** — named ping-schedule records, CDP TLV
  ([pattern E](#e-cdp-named-tlv--leb128-values)). Four record kinds seen
  (`yutl-port/stbd/cntr-port/cntr-stbd-engn-sched`), each carrying nested keys
  `yutl-engn-sched-type`, `stnd-sched`, `opt-sched-1/2/3` and a trailing
  `2f 08 <LEB128 timestamp>`.

**Important (issue #32):** across the full capture the **only** changing bytes
in any config frame are the port/stbd string label and the per-frame timestamp
tail. **No range field, nothing steps at the Cod Rock auto-range transitions.**
The active range is *not* on this stream — the GCV auto-range derives the swath
from depth and never re-broadcasts the resulting range as a value. Reporting a
true active range therefore needs auto-range pinned/disabled over the command
port (#32 deliverable B), not a passive decode.

### 3.6 Command — `d2 07 ef be` (TCP `172.16.3.0:50227`, outbound)

The driver's control path (from `commands.py`). 4-byte magic + `uint32 LE
length` + payload. Builders:

| Command | Payload shape | Notes |
|---------|---------------|-------|
| Transmit on/off | five frames `…01 07 07 01 02 01 0X a9 01 <00\|01>` | one per channel slot; trailing `00`=on, `01`=off |
| Range | per channel 1/2/3 `01 07 08 01 02 01 <ch> 5b <LEB128>` | value in **0.5 mm units** (LEB128 varint) |
| TVG | `01 07 07 01 02 01 00 89 01 <0-3>` | off/low/medium/high; GCV-10-derived |
| Interference | `01 07 07 01 02 01 00 a1 01 <0-3>` | off/low/medium/high |

**Gap (issue #32 deliverable B):** there is **no** auto-range-disable /
force-manual-range builder yet — needed to hold a fixed swath against the
chartplotter's auto-range.

---

## 4. Ground-truth method (M3 multibeam)

Decodes that needed a depth/bathymetry reference were checked against the M3
multibeam in the same sonar bag (same gabby clock). M3 bottom depth per ping =
median over beams of `twtt/2 · sound_speed · cos(rx_angle)` (sound_speed ≈ 1498
m/s from `ping_info`). Boat position is in `/bizzy/odom` — used to locate the two
Cod Rock crossings (the survey lines, ~t174 & ~t561) vs the dock return (end of
bag). The M3 confirms the bin-size ladder and bottom-range varint (§3.1) and
disproved the `0xe4`-as-depth reading (§3.4).

---

## 5. Open questions

- **Imagery sub-header varints (down-look).** Offset-14 = per-ping bottom range
  in ~0.5 mm units (§3.1); there are 1–2 more varints after the `2a` marker that
  also track depth — not yet parsed. The full down-look sub-header structure
  (and whether the side-scan carries an analogous field) is the next decode step.
- **Byte 13 → metres.** Only `0x11`–`0x13` seen; a TCP range-sweep would map the
  full bracket ladder. `decode.py` should also switch generation detection from
  byte 13 to the structural (layer-count) test (§3.1) — a tracked decode bug.
- **`0xe4` sub-type value** — not depth (§3.4); meaning open. A `:50050` capture
  across known device states would decipher it.
- **`00 00` after every magic** — high half of a 32-bit id, or reserved?
- **Node id bodies** (`90 db a2 88 0b`, `d5 a7 f2 8b 0d`), **`e508` value
  records**, and the **`0x00`-status settings block** (`ae 05 c0 …`) — field
  decodes unknown; not yet needed.
- **Nadir depth** — not reported usably by the device. If wanted, a downstream
  node bottom-tracks `sonar_image_down`; draft/tide/transducer-offset correction
  is that node's concern, not the driver's.
