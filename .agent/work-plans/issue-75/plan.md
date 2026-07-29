# Plan: Publish raw framing bytes on a raw topic from sound_speed_bridge

## Issue

https://github.com/rolker/marine_tools/issues/75

## Context

The AML SVS driver's `SoundSpeedReading.raw_bytes` field already captures the
exact framed serial sentence (including terminator) on every parse, success or
failure. It exists today for the UDP passthrough sink. It is not published on
any ROS topic, so deployment bags contain no record of the bytes the probe
actually sent — making post-hoc RCA of garbled sentences require a manual
serial capture.

Scope limit: this is a *per-framed-sentence* passthrough, not a byte-stream
tap. The parser strips inter-sentence padding and drops empty sentences, so
concatenating the published messages does not byte-exactly reconstruct the
wire stream; and a stream that never frames at all (e.g. wrong baud) yields
no readings and therefore publishes nothing. Covering that case needs a tap
inside `_serial_loop` — filed as rolker/marine_tools#77.

Both parsers (`aml`, `regex`) populate `raw_bytes` unconditionally
(`parsers.py:95-110`, `parsers.py:169-176`), so the node can publish it
parser-agnostically from a single call in `_handle_reading()`.

The `garmin_sidescan` node establishes the workspace precedent for the message
type: `std_msgs/UInt8MultiArray`, created at startup, published per received
payload. (Garmin's `~/debug/raw` *name* is not copied — garmin uses `~/` for
all of its topics, while this node publishes bare relative names; see step 2.)
Sound speed sentences are ≲ a few Hz, so always-on is cheap and ensures the
data is in the bag for the deployment where the problem occurs.

## Approach

1. **Add `std_msgs` dependency** — add `<depend>std_msgs</depend>` to
   `package.xml` alongside the existing sensor/diagnostic deps.

2. **Import and create the raw publisher** — in `node.py`, import
   `UInt8MultiArray` from `std_msgs.msg` and create `self._raw_pub` in
   `__init__()` using the same `topic_qos` profile used by the other publishers.
   Topic name: `raw` (bare relative, namespace-scoped) — a sibling of the
   node's existing `sound_speed` / `temperature` / `fluid_pressure` topics,
   so under BizzyBoat's launch it resolves to
   `/bizzy/sensors/sound_speed/raw` (the path unh_echoboats_project11#396
   expects to record). A private `~/raw` name would instead nest it under
   the node name (`<ns>/sound_speed_bridge/raw`), inconsistent with this
   node's own convention.

3. **Publish in `_handle_reading()`** — before the `SoundSpeed` publish,
   publish `UInt8MultiArray(data=reading.raw_bytes)` on `self._raw_pub`
   (`raw_bytes` is already `bytes`; passing it directly matches
   `garmin_sidescan/node.py:709` and avoids boxing each byte into a Python
   int). No condition: always publish regardless of parse success/failure.

4. **Add `test_node.py`** — new test file in `test/` following the
   established node-test pattern in this repo
   (`zda_serial_bridge/test/test_node.py`): an autouse
   `rclpy.init()`/`shutdown()` fixture, `@patch('sound_speed_bridge.node.serial.Serial')`,
   instantiate the real `SoundSpeedBridgeNode()`, swap
   `node._raw_pub = MagicMock()`, call `_handle_reading()` directly, and
   assert on `publish.call_args`, wrapped in `try/finally: node.destroy_node()`.
   The patched serial mock's `read` must return `b''` so the node's
   background serial thread (started in `__init__`, `node.py:115-117`)
   idles harmlessly instead of feeding a MagicMock into `parser.feed()`.
   - `test_raw_publishes_on_parse_success` — valid reading, assert the raw
     publisher received exactly `b'1500.123\r'`.
   - `test_raw_publishes_on_parse_failure` — NaN reading (garbled sentence),
     assert the raw publisher received exactly `b'GARBAGE\r'`.

## Files to Change

| File | Change |
|------|--------|
| `sound_speed_bridge/node.py` | Import `UInt8MultiArray`; create `self._raw_pub`; publish raw in `_handle_reading()` |
| `sound_speed_bridge/package.xml` | Add `<depend>std_msgs</depend>` |
| `test/test_node.py` | New file: two tests for raw publisher (parse success + parse failure) |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Match existing code (ADR-0008 §5) | `std_msgs/UInt8MultiArray` matches `garmin_sidescan`'s raw-passthrough pattern; no new message type needed. Topic *name* follows this node's own bare-relative convention, not garmin's `~/` convention |
| Always-on vs parameter-gated | Issue author recommendation: always-on is correct at ≲ few Hz; garmin's `debug_raw` gate exists for high-rate imagery, not applicable here |
| Tests must cover success and failure | Both paths planned; `raw_bytes` is always set by both parsers, so there is no third branch |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| ADR-0008 (ROS 2 conventions) | Yes | Using standard `std_msgs/UInt8MultiArray`; explicit `package.xml` dependency; topic name `raw` follows the node's bare-relative-topic convention |
| ADR-0017 (AGENTS.md in project repos) | No | No change to repo governance files |
| ADR-0013 (progress.md vocabulary) | Yes | progress.md entry appended with correct schema |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| Add `raw` topic | BizzyBoat bag record list (rolker/unh_echoboats_project11#396) | No — platform side handled in separate issue per issue body |
| Add `std_msgs` dep | package.xml | Yes — step 1 |
| No new marine_interfaces msg | No CMakeLists.txt or msg build machinery needed | N/A — explicitly avoided |

## Open Questions

- [ ] No open questions — plan is review-plan-ready.

## Estimated Scope

Single PR. Three file changes (node.py, package.xml, test/test_node.py). No
new message types, no parameter additions, no launch file changes.
