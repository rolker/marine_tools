"""
Tests for marine_interfaces/Heartbeat extractor.

Construct Heartbeat messages directly and exercise the extractor's
flatten contract — both the scalar-fallback path and the KeyValue
payload path used by /bizzy/marine/heartbeat and /bizzy/marine/status/
mission_manager.
"""

from bag_analysis.extractors.heartbeat import _sanitize_key, extract
from marine_interfaces.msg import Heartbeat, KeyValue


def _kv(key: str, value: str) -> KeyValue:
    kv = KeyValue()
    kv.key = key
    kv.value = value
    return kv


def _build_heartbeat() -> Heartbeat:
    msg = Heartbeat()
    msg.header.frame_id = 'bizzy/marine'
    msg.header.stamp.sec = 1_700_000_000
    msg.header.stamp.nanosec = 250_000_000
    msg.values = [
        _kv('piloting_mode', 'Standby'),
        _kv('marine_autonomy_standby', 'true'),
        _kv('Current Nav Task', 'hover_override'),
        _kv('connected', 'false'),
    ]
    return msg


def test_heartbeat_flattens_keyvalue_payload():
    fields = extract(_build_heartbeat())
    assert fields['piloting_mode'] == 'Standby'
    assert fields['marine_autonomy_standby'] == 'true'
    assert fields['connected'] == 'false'


def test_heartbeat_sanitizes_keys_with_spaces():
    fields = extract(_build_heartbeat())
    # 'Current Nav Task' should sanitize to 'current_nav_task' so
    # it's a valid SQL column name.
    assert fields['current_nav_task'] == 'hover_override'
    assert 'Current Nav Task' not in fields


def test_heartbeat_includes_header_fields():
    fields = extract(_build_heartbeat())
    expected_ns = 1_700_000_000 * 1_000_000_000 + 250_000_000
    assert fields['header_t_ns'] == expected_ns
    assert fields['frame_id'] == 'bizzy/marine'


def test_heartbeat_handles_empty_values_list():
    msg = Heartbeat()
    msg.values = []
    fields = extract(msg)
    # Header still landed; no KeyValue keys present.
    assert 'frame_id' in fields
    assert 'header_t_ns' in fields
    assert 'piloting_mode' not in fields


def test_sanitize_key_normalizes_punctuation_and_case():
    assert _sanitize_key('Current Nav Task') == 'current_nav_task'
    assert _sanitize_key('pattern0000/line0') == 'pattern0000_line0'
    assert _sanitize_key('  Trim Me  ') == 'trim_me'
    assert _sanitize_key('---') == 'unknown'
