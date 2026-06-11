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
`decode.py` and `commands.py` implement his findings. The status (`8e03` nadir
depth) and config (`e508`/`e708`) decodes and the cross-message structural
analysis in this document build on that base, from the 2026-06-10 capture.

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
- **Ground truth for depth:** the M3 multibeam in the same sonar bag (same
  clock) — see [Validation](#4-validation-status-depth).

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
| Status | `8e 03` | 910 | `239.254.2.2:50050` UDP | in | 34 B | ~0.2/s | sub-type + **nadir depth** / settings echo | ✅ this doc |
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
| 13 | **range/scale index** (small integer, steps with range — see below). NOT a generation tag. |
| 14–18 | per-ping token: a 2-byte counter at 14–15 (changes every ping) flanked by range-coupled bytes; also appears in the `d807` marker ([pattern D](#d-a-ping-link-token-ties-markers-to-data)) |

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
| 14–19 | identical | identical | identical | per-ping counter / token |
| 20–22 | `be 93 06` | `be 93 06` | `c4 fa 02` | start of sample/render data (differs by beam content) |

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
  function of range — identical on all three channels, stepping at exactly the
  Cod Rock auto-range transitions (`0x13` deep ~52 m / `0x12` shoal ~31 m /
  `0x11` shallowest).
- The **GCV-10** survey fixture reads byte 13 = **`0x13`** — the *same* value a
  GCV-20 produces in deep water. A value shared across generations cannot be a
  generation tag.

So byte 13 is a **range/scale step index** (higher = longer range). It is the
only field we have *read* that tracks range; it is an index, so converting it to
metres needs a calibrated ladder (only three adjacent steps, `0x11`–`0x13`, are
in the data so far — a TCP range-sweep would map the rest). The active range is
otherwise not broadcast as a value (see §3.5 and #32).

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

#### Deriving range / bin size from the data

Bin count is fixed (~2080 samples) regardless of range, so **bin size** (metres
per sample) carries the range. It can be derived without decoding any header:
find the bottom-return index in the down-look (water-column) channel and combine
with the decoded nadir depth (§3.4):

```
bin_size = depth / bottom_index          # m per sample (down-look is vertical)
range    = n_bins * bin_size
```

2026-06-10: deep ≈ 25 mm/sample → ~52 m range; shoal ≈ 15 mm/sample → ~31 m;
the bottom sat at a near-constant ~28 % of the display (auto-range rescaling),
and bin size returned to the same value at each range step. These numbers are
*derived* (depth + argmax bottom index, both noisy) — e.g. "24.98 mm" is
consistent with a round 25 mm but not distinguishable from it without a TCP
range-sweep calibration.

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

### 3.4 Status — `8e03` (`239.254.2.2:50050`) — **nadir depth**

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
| 17–23 | *sub-type specific* | depth or settings echo |
| 24–29 | `e0 a0 91 0b 01 04` | constant device/message tail |
| 30–31 | u16 LE | monotonic counter (uptime/sequence, ~1/frame) |
| 32–33 | `00 00` | constant |

Constant region confirmed byte-for-byte across all 124 `0x00` + 91 `0xe4`
frames; only offsets 17–23, the 30–31 counter, and byte 9 vary.

**Sub-type `0xe4` — nadir bottom depth (the decode target for #16):**

```
offset 17 18 19 | 20 21 | 22 23
        00 00 00 | DD DD | 00 00      depth = u16_LE(20)   feet = raw/1000
```

`depth_ft = u16 / 1000.0`; `depth_m = u16 / 3280.84`. Feet, not metres
(M3-confirmed — [Validation](#4-validation-status-depth)). Updated on-change
(held between updates), so it lags a live echosounder; offsets 17–19 / 22–23 are
always `00` (a single 16-bit depth). Example: `…02 e4 …00 00 00 d8 ba 00 00…` →
`0xbad8` = 47832 → 47.8 ft.

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

## 4. Validation (status depth)

The `0xe4` depth field was cross-checked against the M3 multibeam in the same
bag. M3 bottom depth per ping = median over beams of
`twtt/2 · sound_speed · cos(rx_angle)` (sound_speed 1469 m/s from `ping_info`).
Pairing all 91 GCV samples to nearest M3 ping (±3 s):

| event | GCV `u16/1000` | M3 depth |
|-------|----------------|----------|
| channel (t+4 s)           | **47.8 ft** | 14.76 m = **48.4 ft** |
| Cod Rock pass 1 (t+154 s) | 30.4 ft | 7.65 m = 25.1 ft |
| channel (t+239 s)         | 50.4 ft | 18.10 m = 59.4 ft |
| Cod Rock pass 2 (t+914 s) | 29.0 ft | 7.07 m = 23.2 ft |

- **Means: GCV 44.3 ft vs M3 43.7 ft** — agree to 0.6 ft.
- **Pearson r = 0.80** — both dip at each Cod Rock pass.
- Metres ruled out: as metres, 44.3 m vs M3 13.3 m (off by 31 m).

~9 ft instantaneous residual RMS is expected (held/on-change field lags live M3;
different transducer footprints over Cod Rock's steep rock; no draft/tide offset
applied). Central tendency exact; feet scale unambiguous.

---

## 5. Open questions

- **Byte-9 vs the transmit flag.** `decode.py`'s `status_transmitting` reads
  status byte 9 as a transmit flag (`0x00` = transmitting, `0x01` = off, from a
  bench capture). This wet capture shows byte 9 = `0x00` / `0xe4`, never `0x01`,
  with the two sub-types interleaved regardless of transmit state — so byte 9 is
  (also) a **message sub-type selector**. Reconcile before relying on either
  read alone; possible latent mis-ID. *(Follow-up; not changed by the depth
  decoder.)*
- **`00 00` after every magic** — high half of a 32-bit id, or reserved? Always
  zero here.
- **Node id bodies** (`90 db a2 88 0b`, `d5 a7 f2 8b 0d`) — MAC/serial mapping
  unconfirmed; only used to tell the two announcing devices apart.
- **`e508` value records** (`27 74 07 72`, `0f 58 04`, `0e 03 04 51 59 04 69`)
  and the `0x00`-status settings block (`ae 05 c0 …`) — field decodes unknown;
  not required for depth or range.
- **`d107` / `d807` payload bodies** beyond the shared tokens — undecoded; not
  needed (driver relies on the real chartplotter and reassembles by channel
  run).
- **Byte 13 (resolved here): a range/scale index, not a generation tag** — see
  §3.1. Open part: it's an *index*; mapping the full ladder to metres needs a TCP
  range-sweep (only `0x11`–`0x13` seen so far). `decode.py` should switch its
  generation detection to the structural (layer-count) test in §3.1 — tracked as
  a decode bug.
- **Range value** — the active range is not broadcast as a value on any captured
  stream; recover it via the down-look bin-size derivation (§3.1) or, once
  calibrated, the byte-13 index. #32-B (pin range over TCP) remains the way to
  *hold* a known swath.
- **Depth datum** — raw transducer bottom-track; draft/tide correction is
  downstream (TF + nav), not in-frame.
