# Plan: Publish raw framing bytes on ~/raw topic from sound_speed_bridge

## Issue

https://github.com/rolker/marine_tools/issues/75

## Context

The AML SVS driver's `SoundSpeedReading.raw_bytes` field already captures the
exact framed serial sentence (including terminator) on every parse, success or
failure. It exists today for the UDP passthrough sink. It is not published on
any ROS topic, so deployment bags contain no record of raw wire traffic — making
post-hoc RCA of baud/framing problems require a manual serial capture.

Both parsers (`aml`, `regex`) populate `raw_bytes` unconditionally
(`parsers.py:95-110`, `parsers.py:169-176`), so the node can publish it
parser-agnostically from a single call in `_handle_reading()`.

The `garmin_sidescan` node establishes the workspace precedent for this pattern:
`std_msgs/UInt8MultiArray` on `~/debug/raw`, created at startup, published per
received payload. Sound speed sentences are ≲ a few Hz, so always-on is cheap
and ensures the data is in the bag for the deployment where the problem occurs.

## Approach

1. **Add `std_msgs` dependency** — add `<depend>std_msgs</depend>` to
   `package.xml` alongside the existing sensor/diagnostic deps.

2. **Import and create the raw publisher** — in `node.py`, import
   `UInt8MultiArray` from `std_msgs.msg` and create `self._raw_pub` in
   `__init__()` using the same `topic_qos` profile used by the other publishers.
   Topic name: `~/raw` (relative, resolves via node namespace like the others).

3. **Publish in `_handle_reading()`** — immediately after computing `stamp_*`
   and before the `SoundSpeed` publish, build a `UInt8MultiArray` with
   `data=list(reading.raw_bytes)` and call `self._raw_pub.publish(msg)`.
   No condition: always publish regardless of parse success/failure.

4. **Add `test_node.py`** — new test file in `test/` that mocks the rclpy Node
   infrastructure and exercises `_handle_reading()` directly:
   - `test_raw_publishes_on_parse_success` — AML-format reading, assert the
     raw publisher received `list(b'1500.123\r')`.
   - `test_raw_publishes_on_parse_failure` — NaN reading (garbled sentence),
     assert the raw publisher received `list(b'GARBAGE\r')`.
   The mock approach is consistent with the existing test pattern (pure Python,
   no rclpy spin), achieved by patching `rclpy.node.Node` methods at import time.

## Files to Change

| File | Change |
|------|--------|
| `sound_speed_bridge/node.py` | Import `UInt8MultiArray`; create `self._raw_pub`; publish raw in `_handle_reading()` |
| `sound_speed_bridge/package.xml` | Add `<depend>std_msgs</depend>` |
| `test/test_node.py` | New file: two tests for raw publisher (parse success + parse failure) |

## Principles Self-Check

| Principle | Consideration |
|---|---|
| Match existing code (ADR-0008 §5) | `std_msgs/UInt8MultiArray` matches `garmin_sidescan`'s `~/debug/raw` pattern; no new message type needed |
| Always-on vs parameter-gated | Issue author recommendation: always-on is correct at ≲ few Hz; garmin's `debug_raw` gate exists for high-rate imagery, not applicable here |
| Tests must cover success and failure | Both paths planned; `raw_bytes` is always set by both parsers, so there is no third branch |

## ADR Compliance

| ADR | Triggered | How addressed |
|---|---|---|
| ADR-0008 (ROS 2 conventions) | Yes | Using standard `std_msgs/UInt8MultiArray`; explicit `package.xml` dependency; topic name `~/raw` follows relative-topic convention |
| ADR-0017 (AGENTS.md in project repos) | No | No change to repo governance files |
| ADR-0013 (progress.md vocabulary) | Yes | progress.md entry appended with correct schema |

## Consequences

| If we change... | Also update... | Included in plan? |
|---|---|---|
| Add `~/raw` topic | BizzyBoat bag record list (rolker/unh_echoboats_project11#396) | No — platform side handled in separate issue per issue body |
| Add `std_msgs` dep | package.xml | Yes — step 1 |
| No new marine_interfaces msg | No CMakeLists.txt or msg build machinery needed | N/A — explicitly avoided |

## Open Questions

- [ ] No open questions — plan is review-plan-ready.

## Estimated Scope

Single PR. Three file changes (node.py, package.xml, test/test_node.py). No
new message types, no parameter additions, no launch file changes.
