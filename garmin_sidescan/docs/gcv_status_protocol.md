# GCV Marine-Network status protocol — `239.254.2.2:50050`

Protocol description for the Garmin GCV-10/20 status broadcast, the source of
**nadir bottom depth** decoded by the `garmin_sidescan` driver (issue
[#16](https://github.com/rolker/marine_tools/issues/16)). It also records the
**negative result** for the active-range question (issue
[#32](https://github.com/rolker/marine_tools/issues/32)): the range is *not*
recoverable from the captured CDP streams.

Every claim here is derived from a wet capture, not assumption — see
[Provenance](#provenance). Encodings the data does not pin are called out
explicitly under [Open questions](#open-questions).

## Provenance

- **Capture:** `bag_2026-06-10T15.54.41_sidescan_raw` (gabby), 2026-06-10
  Piscataqua River deployment (rolker/unh_echoboats_project11#250). 215
  `debug/raw_status` frames + 1,497 `debug/raw_config` frames over 20 min,
  spanning two passes over the **Cod Rock** shoal — each an auto-range
  transition.
- **Ground truth:** the M3 multibeam (`/bizzy/sensors/m3/detections`,
  `marine_acoustic_msgs/SonarDetections`) recorded in the same sonar bag
  (`2026-06-10T17-57-25+00-00`) on the same clock, used to validate the depth
  scale (see [Validation](#validation)).
- The `:50050` (and `:51000`) streams reach the host only when the driver runs
  with `debug_raw:=true`; the relay that forwards them is
  rolker/marine_tools#29.

## Frame envelope

Like every Garmin Marine-Network frame: `<2-byte magic> 00 00` + LE-length(4) +
payload. Status frames carry magic `8e 03` and are a **fixed 34 bytes** (26-byte
payload):

```
offset  0  1  2  3 | 4  5  6  7 | 8 .................................. 33
        8e 03 00 00 | 1a 00 00 00 | <26-byte payload>
        └ magic ┘  └ LE len=26 ┘
```

## Two sub-types, discriminated by byte 9

The `:50050` stream interleaves **two 34-byte variants**, distinguished by the
payload byte at **offset 9**. In this capture byte 9 is only ever `0x00` or
`0xe4`; the two are emitted concurrently throughout the run (not tied to a phase
transition). Shared bytes are identical in both; offsets 17–23 are the
sub-type-specific region.

| offset | bytes | meaning |
|-------:|-------|---------|
| 0–3 | `8e 03 00 00` | magic + pad |
| 4–7 | `1a 00 00 00` | LE payload length (26) |
| 8 | `02` | constant |
| **9** | `00` \| `e4` | **sub-type discriminator** (see below) |
| 10–11 | `0a 0c` | constant |
| 12–13 | `00 00` | constant |
| 14–15 | `03 01` | constant |
| 16 | `00` | constant |
| 17–23 | *sub-type specific* | depth (`0xe4`) or settings echo (`0x00`) |
| 24–29 | `e0 a0 91 0b 01 04` | constant device/message tail |
| 30–31 | u16 LE | **monotonic counter** (uptime/sequence, ~1/frame) |
| 32–33 | `00 00` | constant |

The constant region was confirmed byte-for-byte across all 124 `0x00` and 91
`0xe4` frames in the capture; only the offset-17..23 sub-type region, the
offset-30..31 counter, and byte 9 vary.

### Sub-type `0xe4` — nadir bottom depth (the decode target)

```
offset 17 18 19 | 20 21 | 22 23
        00 00 00 | DD DD | 00 00
                 └ u16  ┘
```

- **Depth** is the little-endian `uint16` at **offset 20–21**.
- **Scale is feet × 1000:** `depth_ft = u16 / 1000.0`; `depth_m = u16 / 3280.84`.
  (Garmin marine units default to feet; M3 cross-check confirms feet, not
  metres — see [Validation](#validation).)
- Updated **on change** (held constant between updates), so it lags a live
  echosounder by the broadcast cadence; treat the timestamp as receive time.
- Offsets 17–19 and 22–23 are `00` in every observed frame (reserved / unused
  high bytes — a single 16-bit depth, not 24/32-bit).

Example frame (depth `0xbad8` = 47832 → 47.8 ft):

```
8e0300001a00000002 e4 0a0c0000030100 000000 d8ba 0000 e0a0910b0104 7453 0000
                   └9┘                       └20┘
```

### Sub-type `0x00` — settings echo (NOT the active range)

```
offset 17 18 19 20 21 22 23
        ae 05 c0 75 ae 05 c0     (constant across the whole capture)
```

This block is **byte-for-byte constant** for the full 20 min, including across
both Cod Rock auto-range transitions. It is the **stale commanded/configured**
value — it matches the driver's own `state` holding `range: 60.0` while the
hardware auto-ranged underneath it. It does **not** report the live active
range. Its exact field decoding (the repeated `ae 05 c0` with `75` between) is
not pinned and is not needed for depth.

### Offset 30–31 — counter (not depth, not range)

A little-endian `uint16` that increments ~once per frame in both sub-types
(low byte at 30 takes all values; high byte at 31 steps `0x53`→`0x54`). An
uptime/sequence counter; decode and ignore for depth/range.

## Validation

The `0xe4` depth field was cross-checked against the M3 multibeam in the same
bag. M3 bottom depth per ping = median over beams of
`two_way_travel_time/2 · sound_speed · cos(rx_angle)` (sound_speed 1469 m/s from
`ping_info`). Pairing all 91 GCV depth samples to the nearest M3 ping (±3 s):

| event | GCV `u16/1000` | M3 depth |
|-------|----------------|----------|
| channel (t+4 s)          | **47.8 ft** | 14.76 m = **48.4 ft** |
| Cod Rock pass 1 (t+154 s) | 30.4 ft | 7.65 m = 25.1 ft |
| channel (t+239 s)        | 50.4 ft | 18.10 m = 59.4 ft |
| Cod Rock pass 2 (t+914 s) | 29.0 ft | 7.07 m = 23.2 ft |

- **Means: GCV 44.3 ft vs M3 43.7 ft** — agree to 0.6 ft.
- **Pearson r = 0.80** — both dip at each Cod Rock pass.
- **Metres ruled out:** interpreting the field as metres gives 44.3 m against
  M3's 13.3 m (off by 31 m).

The ~9 ft instantaneous residual RMS is expected: the GCV field is
held/broadcast-on-change (lags the live M3), the Garmin bottom-track transducer
and the M3 have different footprints over Cod Rock's steep rock, and no
draft/tide offset is applied to either side. The central tendency is exact and
the feet scale is unambiguous.

## The active range is not on the captured streams (issue #32)

For completeness, the related auto-range question: the **active range is never
broadcast as a range** on either CDP stream the relay forwards.

- **`239.254.2.11:51000` config** (`e5/e7 08` CDP) is **static ping-schedule**
  configuration: a device-id heartbeat plus named key/value records
  (`yutl-port/stbd/cntr-engn-sched`, `stnd-sched`, `opt-sched-1/2/3`). Across the
  full capture the only changing bytes are the port/stbd string label and a
  per-frame timestamp tail — nothing steps at the Cod Rock transitions.
- The only range-shaped field anywhere (the `0x00` status settings echo above)
  is the stale commanded value.

The GCV **auto-range derives the swath from the bottom depth** documented here;
it does not re-broadcast the resulting range. Reporting a true active range
therefore requires **pinning/disabling auto-range over the TCP command port**
(force-manual range), not a passive decode — see #32.

## Open questions

- **Byte-9 semantics vs the existing transmit flag.** `decode.py`'s
  `status_transmitting` reads byte 9 as a transmit flag (`0x00` = transmitting,
  `0x01` = off, from an earlier bench capture). This wet capture shows byte 9
  taking `0x00` and `0xe4`, never `0x01`, with the two variants interleaved
  regardless of transmit state — so byte 9 is (at least also) a **message
  sub-type selector**. Reconcile the transmit-state read against the depth
  sub-type before relying on either alone; this may be a latent
  mis-identification in `status_transmitting`. (Tracked as a follow-up, not
  changed by the depth decoder.)
- **`0x00` settings block decode** (`ae 05 c0 75 ae 05 c0`) — believed to be the
  commanded range/settings echo; exact field layout unconfirmed and not needed.
- **Counter units** at offset 30–31 — sequence vs time not distinguished; not
  needed.
- **Depth datum/offset** — the field is a raw transducer bottom-track depth; any
  draft/waterline/tide correction is downstream (TF + nav), not in this frame.

## Decode reference (informative)

```python
import struct

STATUS_MAGIC = b'\x8e\x03'
SUBTYPE_OFFSET = 9
SUBTYPE_DEPTH = 0xe4
DEPTH_OFFSET = 20            # u16 LE, feet * 1000

def status_depth_m(frame):
    """Nadir depth (metres) from a :50050 0xe4 status frame, or None."""
    if frame[:2] != STATUS_MAGIC or len(frame) < DEPTH_OFFSET + 2:
        return None
    if frame[SUBTYPE_OFFSET] != SUBTYPE_DEPTH:
        return None
    raw = struct.unpack_from('<H', frame, DEPTH_OFFSET)[0]
    return raw / 1000.0 / 3.280839895   # feet*1000 -> metres
```
