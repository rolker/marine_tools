# Garmin GCV Marine-Network protocol reference

Every message the `garmin_sidescan` driver sees or sends on the Garmin Marine
Network (GCV-10 / GCV-20), in one place. Every claim is grounded in a capture
or in the driver source; provenance and anything unverified are called out
explicitly.

The foundational reverse engineering — the imagery stream (`eb07`/`d807`
render-layer model) and the TCP command frames (transmit/range/TVG/
interference) — is the work of **Dan Tauriello**, validated live on both the
GCV-10 and GCV-20; the driver's `decode.py` and `commands.py` implement his
findings and unit-test them, so this document summarizes and cross-links them
rather than restating byte layouts that live in code. The status/config
decodes and the structural analysis here come from the wet captures below.

**Captures** (gabby, `debug_raw:=true` bags unless noted):

| Capture | Contents | Used for |
|---|---|---|
| GCV-10 survey pcap (Dan, `dumpcap_file.pcap`) | imagery, 2 channels (1/3), 20 m commanded range | render-layer model, GCV-10 fixtures, grammar + v2=20.00 m validation |
| `gcv10_20260605-050008.pcap` (bench) | imagery, 3 channels (0/2/5) | GCV-10 grammar validation; down-look beam byte `0x0d` confirmed on GCV-10 |
| `gcv20_rangesweep_20260605-024550.pcap` (bucket) | all planes, **operator range sweep 3→15 m in 1 m steps + ramp to 50 m** | v2 = the commanded range (exact integers, staircase matches operator actions); config/status behavior under manual range changes |
| `gcv20_settings_20260605-031143.pcap` (bucket) | all planes, TVG steps + preset/mode toggles + power-cycle | chartplotter-side stream inventory (§4.7), `cdp_current_preset_tab` record, beam gating |
| 2026-06-05/09 GCV-20 bench/wet | imagery | GCV-20 echo layer, trailer fix (#26), fixtures |
| `bag_2026-06-10T15.54.41` | all 4 streams, 20 min, **2 Cod Rock auto-range transitions**, M3 in same bag | sub-header varints (M3-validated), status/config decode, auto-range behavior |
| `bag_2026-06-11T15.35.08` | all 4 streams, 30 min | independent re-verification sweep: envelope 100%, rates/lengths, d807 channel tag, rare status sub-types |
| `bag_2026-06-15T14.59.41` (Lake Massabesic survey) | all 4 streams, 20 min, 2 auto-range transitions | render layers are length-delimited fields; `#26` regression (record-counter high byte `0x43` → `rfind(0x43)` residual); `gcv20_counter43_pings.bin` fixture |

---

## 1. The envelope

Every frame, **both directions**, is one 8-byte TLV header plus payload:

```
<u32-LE message id>  <u32-LE payload length>  <payload>
└──── offset 0-3 ───┘└────── offset 4-7 ─────┘└─ 8.. ──┘
```

- **Length** is always `total_length − 8`. Verified on **100 %** of frames:
  427,767/427,767 across all six inbound types in the 2026-06-11 bag, and the
  full 2026-06-10 capture. `commands.py` constructs outbound frames with the
  same rule.
- **Inbound ids** have a zero high half, so on the wire they read as a 2-byte
  magic followed by `00 00` (e.g. imagery data `eb 07 00 00` = id `0x000007EB`).
- **The outbound command id** is `d2 07 ef be` — as a u32-LE, **`0xBEEF07D2`**:
  the same envelope with the high half set to a `0xBEEF` cookie instead of
  zero. This is why the command frame previously looked like a "4-byte magic
  with no pad": there is no pad anywhere, just a 32-bit id whose high half is
  `0x0000` on the bus and `0xBEEF` from a commanding host.
  *Caveat*: whether the device requires `0xBEEF` (vs. accepting a zero high
  half) is untested — treat it as required.

The low half groups messages into planes (high byte `0x07` =
imagery/command, `0x08` = config; status sits on its own):

| id (LE) | hex | bytes on wire | message | plane |
|---:|-----|---------------|---------|-------|
| 2001 | `0x07d1` | `d1 07 00 00` | keepalive | imagery |
| 2002 | `0x07d2` | `d2 07 ef be` | command (outbound, `0xBEEF` high half) | command |
| 2008 | `0x07d8` | `d8 07 00 00` | channel marker | imagery |
| 2027 | `0x07eb` | `eb 07 00 00` | imagery data | imagery |
| 2277 | `0x08e5` | `e5 08 00 00` | config record | config |
| 2279 | `0x08e7` | `e7 08 00 00` | config heartbeat | config |
| 910 | `0x038e` | `8e 03 00 00` | status | status |

---

## 2. Message summary

All multicast streams are listen-only; the TCP command path is the only thing
the driver sends. Lengths and rates are measured (2026-06-10 + 2026-06-11
captures); imagery sizes and rates vary with depth/range and ping rate.

| Message | id | Transport | Dir | Len (B) | Rate | Purpose | Decode |
|---------|-----|-----------|:---:|---------|------|---------|--------|
| Imagery data | `eb07` | `239.254.2.1:50220` UDP | in | 544–956 | ~200/s pinging | side/down scan-line sample packets | ✅ `decode.py` |
| Channel marker | `d807` | same | in | 14–16 | 3 × ping rate | delimits one channel's packet run; two-field record: channel tag + that run's bottom range (§3.C) | ✅ |
| Keepalive | `d107` | same | in | 22 | 1/s | chartplotter-master keepalive that sustains pinging | ⚠️ envelope only |
| Status | `8e03` | `239.254.2.2:50050` UDP | in | 34 | 0.2/s | tx flag / settings echo + held `0xe4` value (NOT depth) + rare one-shot sub-types | ◐ §4.4 |
| Config heartbeat | `e708` | `239.254.2.11:51000` UDP | in | 19 | 1/s | device-id heartbeat (static) | ✅ static |
| Config record | `e508` | same | in | 168/173 | bursts (~0.4/s avg) | named CDP ping-schedule key/values | ◐ structure ✅, values partial |
| Command | `d207` (`0xBEEF` high) | `172.16.3.0:50227` TCP | **out** | varies | on demand | transmit / range / TVG / interference | ✅ `commands.py` |

Decode legend: ✅ understood · ◐ partial · ⚠️ envelope only.

---

## 3. Shared patterns

### A. The record grammar: tagged fields

Payloads are **tagged records**, one grammar across every frame type:

```
record :=  <field count>  field*
field  :=  <tag byte> <value>
tag    :=  (field# << 3) | L     L = 1..6 → value is an L-byte LEB128 varint
                                 L = 7    → next byte is an explicit length,
                                            then that many value bytes
```

Verified with **zero exceptions on 697k+ frames across five captures and both
generations** (every eb07 sub-header, every d807 marker, and the
d107/e708/e508 payloads walk it exactly). Worked examples:

```
d107 keepalive:  04 | 01 03 | 0d <5-byte id> | 12 8a 18 | 19 03
                 cnt  f0=3    f1 = node id     f2 (2 B)    f3 = 3
e708 heartbeat:  03 | 01 03 | 0d <5-byte id> | 11 01
                 cnt  f0=3    f1 = node id     f2 = 1
e508 record:     05 | 01 03 | 0d <5-byte id> | 1f 15 <21-B name> | 27 74 <116-B body> | 2f 08 <8-B timestamp>
                 cnt  f0=3    f1 = node id     f3, L=7 escape      f4, L=7 escape       f5, L=7 escape
d807 marker:     02 | 0x10|L <v1> | 19 <channel>
                 cnt  f2 = bottom   f3 = channel
```

Consequences worth naming:

- The infamous **eb07 byte 13** — read first as a "generation tag", then as a
  "coarse range bracket" — is **field 2's tag**: `0x11/0x12/0x13` is just the
  v1 varint's byte length, stepping when the bottom range crosses the LEB128
  length boundaries at **4.10 m and 8.19 m**. There is no bracket ladder.
- The "node descriptor" opening `<n> 01 03 0d <5 bytes>` is the grammar:
  count `n`, field0 = 3 (a protocol version?), field1 = the sender's **5-byte
  node id** (`0x0d` = field1, length 5). Two ids on the bus — `90 db a2 88 0b`
  (GCV, config plane) vs `d5 a7 f2 8b 0d` (chartplotter, keepalive) —
  byte-identical across every frame of the 2026-06-11 capture (1788 / 1799).
- Field numbers are scoped **per message type** (field2 is v1 in imagery
  frames, a 2-byte unknown in the keepalive).

### B. LEB128 varints, 0.5 mm range unit

Unsigned base-128 varints are the protocol's number format: the TCP range
command value, the imagery sub-header's range fields (§4.1), and the
`e508` record timestamps all use them. Range-like values are in **0.5 mm
units** (the TCP range command's unit).

### C. The `d807` marker: channel tag + that run's range fields

The common marker payload (14–15 B total frame) is a two-field record:

```
02  |  0x10|L <v1 varint>  |  19 <channel>
cnt    field2 = bottom range   field3 = channel
```

Markers **bracket** channel runs — they appear in close/open pairs at run
boundaries (visible in the GCV-20 fixture: `…19 02`, `…19 01` between a ch2
and a ch1 run). Each marker **tags an adjacent run's channel** (field3,
cycling `00`/`01`/`02` in exactly equal thirds — 20,330/20,329/20,331 in the
2026-06-11 capture) and carries **that run's** v1 bottom range (§4.1).
Verified per-marker against both neighbours on the full 2026-06-11 capture:
**94 %** match the preceding (79 %, close) or following (15 %, open) run's
channel *and* v1 exactly; the residual is packet loss plus the sub-form below
parsed as if common-form.

- A 16-byte **telemetry** sub-form (`02 0c <float32 LE> 19 <channel>`,
  ≈ 1.2 % of markers, in channel triplets ~0.7 Hz) carries **field1 as an
  IEEE-754 float32 = the transducer surface water temperature (°C)**. The same
  value appears on all three channels of a triplet (a device-wide scalar).
  Validated two ways, each within one water body / day: against an AML CTD cast
  (2026-06-10 pier, 020c 15.5 °C vs cast surface 16.0–16.5 °C) and against the
  boat's own sound-velocity sensor (2026-06-12, the temperature predicts the
  measured sound speed within 1–3 m/s). It reads the warm surface skin the
  hull transducer sits in, not the bulk water.

The delimiter sub-form ends a channel run; the telemetry sub-form does **not** —
it is interleaved mid-run, so flushing on any `d807` (the original behaviour)
split single pings into two fragments. The assembler now flushes only on the
delimiter sub-form (`decode.is_run_delimiter`) and decodes the telemetry
temperature (`decode.marker_temperature_c`); the run's own `eb07` sub-headers
carry the channel/range authoritatively, and the next channel's first packet
backstops a dropped delimiter (the stream cycles channels). See issue #37.

---

## 4. Per-message detail

### 4.1 Imagery data — `eb07` (`239.254.2.1:50220`)

Side-scan and down-look sample packets; a scan line is reassembled from a run
of same-channel packets bracketed by `d807` markers. The render-layer model
(per-generation echo extraction, GCV-10 "dark layer" vs GCV-20 16-bit first
layer) was reverse-engineered by **Dan Tauriello**; the render layers are
length-delimited fields of the record grammar (see "Render layers are
length-delimited fields" below), fully unit-tested in **`decode.py`** — refer
there for the authoritative layout. Key sub-header bytes (full-frame offsets,
after the 8-byte envelope):

| offset | meaning |
|-------:|---------|
| 8 | render-layer / beam-type byte: `0x0d` down-look (water column) on **both generations** (GCV-20 wet + GCV-10 bench verified), `0x0e` (GCV-20) / `0x0f` (GCV-10) side-scan |
| 9–10 | `01 03` — field0 = 3 (grammar §3.A) |
| 11–12 | `09 <channel>` — field1 = channel number (maps vary by unit/config: GCV-20 0/1/2; GCV-10 survey 1/3; GCV-10 bench 0/2/5) |
| 13… | fields 2–6 of the record grammar — see below |

Frequency and a per-ping timestamp are **not** carried; the driver takes
frequency from a parameter and stamps scan lines with receive time.

#### The sub-header fields: v1 bottom range, v2 display range, v3

The sub-header continues the record grammar (§3.A); range values are LEB128
varints in **0.5 mm** units. `parse_subheader` walks it field-by-field —
verified with zero exceptions on every eb07 frame of five captures (697k+):

```
field2  (tag 0x10|L):  v1  BOTTOM RANGE   (M3: corr 1.00 / ratio 1.013; bucket: 0.38 m)
field3  (tag 0x19):    value 0 in imagery frames (the channel in d807 markers)
field4  (tag 0x20|L):  v2  DISPLAY RANGE  (this channel's scan extent)
field5  (tag 0x28|L):  v3  ~64–100 mm     (near-field / start range?; corr 0.94; meaning TBD)
field6  (tag 0x31):    value 2
field7  (tag 0x3f, L=7):  RENDER LAYER 1 — length-delimited (see below)
```

- **v1 = measured bottom range**, per ping, **shared across all channels**
  (the boat's depth; near-exact vs M3 at 7–20 m, and 0.38 m in the bucket
  test). The driver publishes it as `~/nadir_depth` (issues #16/#35).
- **v2 = this channel's own display range**, and it is **the real range under
  every control regime**, verified three ways: it tracks both Cod Rock
  **auto-range** transitions (side-scan 47.8 → 30.4 → 50.4 → 29.8 → 48.9 m,
  2026-06-10) while the driver's commanded mirror held `60.0`; it reads the
  **operator's chartplotter range steps as exact integers** (5→4→3,
  4,5,…,15 m staircase then the ramp to 50 m — the 06-05 bucket sweep); and
  Dan's survey reads a flat **20.00 m**. Per channel: side-scan = the
  across-track slant range (= commanded range when one is set); **down-look =
  the water-column depth range, always auto** (it ignored the bucket sweep,
  holding its own 1.84 m). So **`bin_size = v2 / n_bins`** must use the
  matching channel's v2. The driver derives the published
  `RawSonarImage.sample_rate` from it (#35).
- **v3** small (~64–100 mm), weakly depth-correlated — a candidate
  near-field/blanking or start-range term (§6). GCV-10 encodes it as a 1-byte
  varint (tag `0x29`), GCV-20 typically 2-byte (`0x2a`) — same field, just the
  length bits.

`n_bins` (~2034) is **not** a range control — its short values
(848/1148/1500/1748 ≈ 2034 − N×309) are dropped-packet assembly artifacts.

#### Render layers are length-delimited fields (no magic, no trailer)

The render layers continue the **same tagged-record grammar** (§3.A) — they are
`L=7` (length-delimited) fields. There is **no separate "trailer" and no
sample-region search**: a layer's byte length is declared inline. Walking the
grammar from offset 9 consumes a real packet **exactly to the envelope end**
(verified on the 2026-06-15 bag and both fixtures), e.g. a GCV-20 side-scan
packet:

```
field7   tag 3f  L=7  len=602  value = <LEB 600><600 sample bytes>   render layer 1 (16-bit echo)
field8   tag 43       3 bytes  = device record counter (monotonic, +1 per scan line)
field9   tag 4a       1–2 B    = packet's cumulative sample offset within the line
field10  tag 52       2 bytes  = 80 10  (a constant)
field11  tag 5a       2 bytes  = per-ping range echo (≈ field2 range)
field12  tag 67  L=7  len=302  value = <LEB 300><300 sample bytes>   render layer 2 (lower-res)
field13  tag 6b       3 bytes  = closing record
```

So the bytes `decode.py` once treated as **fixed magic** are the layer's LEB128
**length** pair: `da 04 d8 04` decodes as varints `602` (field length) then
`600` (inner sample-byte count); `f2 03 f0 03` → `498`/`496`; `ae 02 ac 02`
(later layer) → `302`/`300`. They were constant only because the per-packet
sample count was constant in the early captures — a different range/firmware
emits a different length, which the old magic match would miss.

The records once treated as an **appended "trailer"** (`43 … 4a … 52 80 10 …`)
are simply **fields 8–11 between the two layer fields** — not part of the
samples. `echo_layer` reads `field7`'s declared length and returns exactly its
samples; the GCV-10 (`dark_layer`) path is unchanged (its high-res "dark" layer
is the last layer, bounded by the packet end).

> **Regression history (issue #26).** The original fix searched for the layer
> end: `rfind(52 80 10)` then `rfind(0x43)` for the trailer opener. But field8's
> 3-byte value is the **record counter**, whose high byte is `0x43` for ~65,536
> consecutive records (~33 min). When it is, `rfind(0x43)` locks onto that inner
> byte instead of the true opener and leaves a bright `0x__43` residual sample at
> **every** packet-concatenation boundary — the comb of vertical lines seen for
> the first ~6.5 min of the 2026-06-15 Lake Massabesic survey, which cleared on
> its own when the counter rolled `0x43FFFF → 0x44xxxx`. Reading the field length
> removes the search (and the bug) entirely.

#### Distinguishing the beams (down / port / starboard)

Diffing the three channels' sub-headers within one range/time window
(2026-06-10):

| offset | port (ch0) | stbd (ch1) | down (ch2) | note |
|-------:|:----------:|:----------:|:----------:|------|
| 8 | `0e` | `0e` | **`0d`** | beam type — separates **down vs side-scan** only |
| 9–11 | `01 03 09` | `01 03 09` | `01 03 09` | field0 = 3, field1 tag |
| **12** | **`00`** | **`01`** | **`02`** | **channel number** (field1 value) |
| 13 | `13` | `13` | `13` | field2 tag — same on all channels because v1 is shared |

- **Down vs side-scan** is intrinsic at **offset 8** (`0x0d` down vs
  `0x0e`/`0x0f` side; the `0x0e`/`0x0f` split is generation, not side).
- **Port vs starboard:** the two side-scan channels are byte-for-byte
  identical in the sub-header **except the channel number at offset 12**, so
  the driver maps side from the channel number via the `port_channels` /
  `stbd_channels` params. Whether an intrinsic side marker exists elsewhere is
  **not established**; the channel→side mapping varies by unit/config (GCV-20
  `0/1/2`; GCV-10 survey `3/1`; GCV-10 bench `0/2/5`).

#### Byte 13: a generation tag that wasn't, then a range bracket that wasn't

`decode.py` historically read offset 13 as a GCV-10-vs-GCV-20 "generation
tag"; the 2026-06-10 capture disproved that and recast it as a "coarse range
bracket" (`0x11`–`0x13`, stepping with range). The record grammar (§3.A)
dissolves the bracket too: byte 13 is **field2's tag**, and its low bits are
the **v1 varint's byte length** — it "steps with range" only because the
bottom range crosses the LEB128 length boundaries at **4.10 m / 8.19 m**
(1→2→3 bytes). Both prior readings were correlates of depth. Note device
**gain steps at these same transitions** (§6) — the only observable that
still keys on this byte.

#### Generation is recoverable from packet structure (range-independent)

The real GCV-10/GCV-20 difference is **structural**, in the render layers, and
independent of range:

| stream | render layers | later-layer (`SH`/`SHS`) headers | echo | samples |
|--------|---------------|----------------------------------|------|---------|
| **GCV-10** side-scan | 3 | **2** | last ("dark") layer | 8-bit (`UINT8`) |
| **GCV-10** water-column | 1 | **0** | first (`FH`) layer | 16-bit LE (`UINT16`) |
| **GCV-20** (all) | ≤2 | **0 or 1** | first (`FH`) layer | 16-bit LE (`UINT16`) |

Verified on **two independent GCV-10 captures** (`dumpcap_file.pcap` survey,
7,560 packets; `gcv10_20260605-050008.pcap` bench, 4,074 side-scan packets) and
every GCV-20 capture (fixtures *and* the 06-10 bag): every GCV-10 **side-scan**
packet has two later-layer headers; every GCV-20 packet has at most one — so the
`SH`/`SHS` count is a **side-scan** generation signal (`decode.generation_from_layers()`).

> **The down-look (water column) is the exception, and it is 16-bit on both
> generations.** A GCV-10 water-column (`0x0d`) packet is a **single echo layer**
> (one `FH`, no `SH`/`SHS`), so `generation_from_layers()` reports it as `gcv20`.
> That is *right for the extractor* — it really is a 16-bit echo layer like any
> GCV-20 channel — but it means decode must pick the extractor **per packet**
> from its own structure (`PingAssembler`), not once per device: the 3-layer dark
> form → `dark_layer`/8-bit; every echo form (all GCV-20 channels **and** the
> GCV-10 water-column) → `echo_layer`/16-bit. A single device-wide extractor
> blanked the GCV-10 water-column (`dark_layer` finds no `SH`/`SHS` → empty) —
> issue #60.

#### Sample width verified empirically (8-bit side-scan / 16-bit echo)

The widths above — not just the layer *count* — were confirmed (2026-06-20) by a
byte-parity + autocorrelation test on the extracted layer, with a GCV-20
positive control:

- **Test.** Split the layer bytes into even/odd-index streams and compare their
  spread/entropy, plus lag-1 vs lag-2 autocorrelation. A 16-bit-LE echo shows a
  **smooth-MSB / noisy-LSB** split between parities and `lag1 < lag2`; a true
  8-bit echo shows **identical parity stats** and `lag1 > lag2` (adjacent bytes
  are samples).
- **GCV-10 side-scan = 8-bit** ("dark" layer), both captures — survey: even/odd
  entropy 7.24 ≈ 7.25, `lag1 0.93 > lag2 0.87`; bench: even/odd std 87.7 = 87.7,
  `lag1 0.997 > lag2 0.991`.
- **GCV-10 water-column = 16-bit LE** (bench `0x0d`): MSB std 36.5 < LSB std 74.2,
  `lag1 −0.29 < lag2 0.41`.
- **GCV-20 = 16-bit LE** (06-19 wet bag): MSB std 13.0 ≪ LSB std 76.7,
  `lag1 −0.41 < lag2 0.50`.

### 4.2 Channel marker — `d807`

14–16 byte delimiter emitted between channel runs; payload decoded in
[§3.C](#c-the-d807-marker-channel-tag--that-runs-range-fields). The
assembler treats it as "flush the current channel's accumulation."

### 4.3 Keepalive — `d107`

22-byte frame, exactly 1 Hz, from the chartplotter master. Sustains pinging
(the GCV will not run without the master's hardware enable + keepalive).
Payload `04 01 03 0d <chartplotter node id> 12 8a 18 19 03` — the node
descriptor of §3.A; byte-identical across the full 2026-06-11 capture. The
body after the id is not decoded; not needed (the driver does not synthesize
keepalives — it relies on the real chartplotter).

### 4.4 Status — `8e03` (`239.254.2.2:50050`)

Fixed 34 bytes **from the GCV**. (The chartplotter broadcasts its own
**70-byte** `8e03` variant on the same group — see §4.7; boat-side bags never
show it because the Marine-Network **proxy deliberately filters `:50050` to
the GCV's source IP** — `tools/garmin_marine_network_proxy.py`. Relaying the
chartplotter frames too would need a proxy option.) The stream multiplexes
**sub-types**, discriminated by the payload byte at **offset 9**. Shared
frame:

| offset | bytes | meaning |
|-------:|-------|---------|
| 0–3 | `8e 03 00 00` | id + zero high half |
| 4–7 | `1a 00 00 00` | LE length (26) |
| 8 | `02` | constant |
| **9** | sub-type | `0x00`/`0x01` settings echo (+ tx flag), `0xe4` held value, rare one-shots below |
| 10–16 | `0a 0c 00 00 03 01 00` | constant |
| 17–23 | *sub-type specific* | |
| 24–29 | `e0 a0 91 0b 01 04` | constant device/message tail |
| 30–31 | u16 LE | monotonic counter |
| 32–33 | `00 00` | constant |

Constant regions verified byte-for-byte on every status frame of both wet
captures (215 + 359 frames).

**Sub-type `0x00` / `0x01` — settings echo and transmit flag.** `0x00` =
transmitting, `0x01` = off (the only sub-types the driver reads —
`decode.status_transmitting`; all others return "unknown" so they cannot flap
the flag). Offsets 17–23: `ae 05 c0 75 ae 05 c0`, **constant** through both
wet captures including the auto-range transitions — a stale
commanded/configured echo, NOT the active range. On the 06-05 **bench**
captures, though, this region cycles through many values including
ASCII-looking fragments (`",13"`, `"nit"`, `"7,R"`, zeros…) — possibly a
windowed text/diagnostic stream. Field decode open (§6) and not needed by the
driver.

**Sub-type `0xe4` — a held value of unconfirmed meaning (NOT depth).** A u16
LE at offset 20 (offsets 17–19/22–23 zero). Cross-checked against the M3: it
is **held** for tens of seconds, lags the M3 nadir by 2–7 m, and does not
track the bottom — its earlier reading as a nadir depth is disproven (#16
history). The real per-ping bottom range is the imagery sub-header **v1**
(§4.1), which the driver publishes as `~/nadir_depth`.

**Rare one-shot sub-types** (2026-06-11 capture, one frame each — meaning
unknown, §6):

| t (s) | byte 9 | offsets 16–23 | shape |
|------:|--------|----------------|-------|
| 419.6 | `0x09` | `00 53 8e 3a 55 4c cf 3a` | two small float32-like values? |
| 679.9 | `0x1f` | `00 16 86 3c 9f 8a d8 3c` | same shape as `0x09` |
| 1005.1 | `0xed` | `00 00 00 00 94 89 00 00` | same zeros+u16@20 shape as `0xe4` |

### 4.5 Config — `e708` heartbeat + `e508` records (`239.254.2.11:51000`)

The chartplotter's CDP config broadcast (it owns range/freq/schedule on the
bus):

- **`e708` (19 B, 1 Hz)** — static device-id heartbeat:
  `03 01 03 0d 90 db a2 88 0b 11 01` (§3.A).
- **`e508` (168 / 173 B, in bursts)** — named ping-schedule records, ASCII-keyed
  TLV: `<tag> <len> <key-ascii> 00 <value-record> …` closing with a
  `2f 08`-tagged LEB128 timestamp. Four record kinds seen
  (`yutl-port/stbd/cntr-port/cntr-stbd-engn-sched`), each carrying nested keys
  `yutl-engn-sched-type`, `stnd-sched`, `opt-sched-1/2/3`.
- **`e508` event record `cdp_current_preset_tab_v1:<NN>` (59/61 B)** —
  broadcast only when the chartplotter's active preset tab changes (which is
  why steady-state captures never show it). The 06-05 settings capture shows
  tab `:48`↔`:49` switches correlating exactly with **which beams stream**
  (all three ↔ down-only ↔ subsets) — the preset tab gates the transmitted
  arrays. The rangesweep capture announces `:49` just before the operator's
  sweep begins.

**Important (issues #32/#35):** across the full 06-10 capture the **only**
changing bytes in any config frame are the port/stbd string label and the
per-frame timestamp tail. **No range field, nothing steps at the Cod Rock
auto-range transitions — and the record bodies stayed byte-identical through
the entire 06-05 operator range sweep too**, so range is not on this stream
under auto OR manual control (the chartplotter must command the GCV by
another path, presumably its own TCP `:50227` session). The active range
**is** passively available: each channel's per-ping sub-header **v2** (§4.1)
is that channel's true active display range. The driver derives the published
`RawSonarImage.sample_rate` from it (#35). Pinning/disabling auto-range over
the command port (#32 deliverable B) remains open as an optional survey-ops
capability — for *holding* a fixed swath, no longer for *knowing* it.

### 4.6 Command — id `0xBEEF07D2` (TCP `172.16.3.0:50227`, outbound)

The driver's control path (from `commands.py`; envelope in §1). Builders:

| Command | Payload shape | Notes |
|---------|---------------|-------|
| Transmit on/off | five frames `…01 07 07 01 02 01 0X a9 01 <00\|01>` | one per channel slot; trailing `00`=on, `01`=off |
| Range | per channel 1/2/3 `01 07 08 01 02 01 <ch> 5b <LEB128>` | value in **0.5 mm units** (LEB128 varint) |
| TVG | `01 07 07 01 02 01 00 89 01 <0-3>` | off/low/medium/high; GCV-10-derived |
| Interference | `01 07 07 01 02 01 00 a1 01 <0-3>` | off/low/medium/high |

**Gap (issue #32 deliverable B):** there is **no** auto-range-disable /
force-manual-range builder — needed only to hold a fixed swath against the
chartplotter's auto-range.

**Transmit safety — out-of-water auto-stop (issue #41).** The driver does not
gate transmit on an external sound-speed reading; it relies on the GCV ceasing
to ping on its own when the transducer is out of the water, so a dry transducer
cannot overheat. **Source: field observation (R. Arsenault) — the unit stops
pinging in air.** This is **not yet** independently confirmed against a Garmin
spec or a recorded bench test. **TODO: bench-confirm the dry → transmit-stop
latency and cite it here.** Until then, treat `transmit_on_startup` and any
commanded transmit as unprotected against a dry transducer — only energize with
the transducer submerged.

### 4.7 Chartplotter-side streams (bench inventory; not consumed by the driver)

The 06-05 bucket captures show the chartplotter (`172.16.6.64`) broadcasting
on several groups beyond `:51000`. Inventoried for completeness — none
carries the range (checked against the operator range sweep) and the driver
consumes none of them:

| Stream | id | Len | Rate | Content |
|---|----|----|------|---------|
| `239.254.2.2:50050` | `8e03` | **70** | 0.2/s | chartplotter status: nine-entry `<u8 key> 00 <u16 value>` settings table (keys `1a/03/16/08/43/17/45/44/15`; static through the TVG/preset session — not TVG), the same cycling ASCII-fragment window at 17–19 as the GCV frames, device id + counter tail |
| `239.254.2.4:8322` | — | 108 | 0.5/s | **position broadcast**: lat/lon as float64 radians at offsets 20–35 (reads 43°N, −71°W on the bench), plus undecoded floats |
| `239.254.2.18:51400` | `050a` | 14/16 | 0.5/s | constant pair (record grammar; field values static) |
| `239.254.2.22:51950` | `4719`/`4819` | 33/26 | 0.4/s | heartbeats — only a u16 counter changes |
| `239.254.2.14:50615` | `fd09` | 16 | 4 every 10 s | periodic 4-frame enumeration burst, constant pattern |
| `224.0.0.1:50030`, `233.89.188.1:10001`, SSDP `:1900` | — | — | — | discovery/beacon traffic |

**Capture-topology caveat:** the 06-05 sniff port saw only switch-flooded
traffic — these captures contain **zero unicast** between chartplotter and
GCV, so the actual command sessions (range, settings) are invisible in them.
A port-mirror or inline tap is required to capture those (§6, #32-B).

---

## 5. Ground-truth method (M3 multibeam)

Decodes that needed a depth/bathymetry reference were checked against the M3
multibeam in the same sonar bag (same gabby clock). M3 bottom depth per ping =
median over beams of `twtt/2 · sound_speed · cos(rx_angle)` (sound_speed ≈ 1498
m/s from `ping_info`). Boat position is in `/bizzy/odom` — used to locate the
two Cod Rock crossings vs the dock return. The M3 confirms the sub-header v1
bottom range and v2 scaling (§4.1) and disproved the `0xe4`-as-depth reading
(§4.4).

---

## 6. Open questions

- **v3** (~0.06–0.1 m, weakly depth-coupled) — near-field/blanking/start-range?
- **`0xe4` value** — held, not depth; meaning open. **New 2026-06-11:**
  one-shot sub-types `0x09`/`0x1f` (float-pair-like) and `0xed` (same shape as
  `0xe4`) — a `:50050` capture across known device state changes (transmit
  on/off, range commands, frequency switch) would decipher the family.
- **`0x00`-status offsets 17–23**: constant `ae 05 c0 75 ae 05 c0` on the
  boat, but cycling values with ASCII fragments on the bench — windowed
  text/diagnostic stream? Sequence-reassemble the 06-05 bench frames.
- ~~**`d807` field1 sub-form** (`02 0c <4 raw bytes> 19 <ch>`) — the 4-byte
  value's meaning.~~ **Resolved (issue #37): float32 surface water temperature
  (°C).** See §3.C.
- **`0xBEEF` command high half** — required by the device, or cosmetic? One
  TCP experiment (send a `0x0000`-high command); don't test on a live survey
  unit.
- **The chartplotter→GCV range-command path.** Range is not re-broadcast on
  `:51000` (auto or manual) nor `:50050`. The 06-05 pcaps were checked for
  the command session (2026-06-11): they contain **zero TCP and zero
  GCV↔chartplotter unicast** — the sniff port only saw switch-flooded
  (multicast/broadcast) traffic, so the unicast command session is invisible
  at that capture point. **#32 deliverable B therefore needs a port-mirror /
  inline-tap capture** of the chartplotter↔GCV link; nothing more can be
  extracted passively from the existing captures.
- **The 70-byte chartplotter `8e03` key-value table** (§4.7): nine keys,
  static through the TVG/preset session — meanings unknown. A mirrored or
  longer capture across more setting changes would map them.
- **Gain steps at the field2 tag-length transitions** (the ex-"bracket", i.e.
  when the bottom range crosses 4.10 m / 8.19 m, and at auto-range changes) —
  a range/depth-coupled TVG/AGC applied before sending samples. Whether a
  separate gain field exists is open. The driver publishes samples as-is;
  gain normalization is a downstream concern.
- **Field semantics not yet needed**: keepalive field2 (`8a 18`) and
  field3 = 3; e708 field2 = 1; field0 = 3 everywhere (version?); node-id
  bodies (`90 db a2 88 0b`, `d5 a7 f2 8b 0d`); the `e508` nested body values
  (mechanically walkable now via the §3.A grammar).
