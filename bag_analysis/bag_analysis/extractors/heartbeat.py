"""
marine_interfaces/Heartbeat extractor.

Heartbeat messages in this stack carry their actual payload in a
`values: sequence<KeyValue>` field, which previous code dropped
entirely. Different emitters use different key sets:

- `/bizzy/marine/heartbeat`              piloting_mode, armed, guided, ...
- `/bizzy/marine/status/mission_manager` Navigator, "Current Nav Task", ...

This extractor flattens the values list into wide columns per topic;
the KeyValue keys (which can contain spaces, mixed case, etc.) are
sanitized to be SQL-safe column names so downstream queries can do
`SELECT piloting_mode FROM t_bizzy_marine_heartbeat`.

Scalar top-level fields are also extracted defensively (in case a
future Heartbeat schema adds them); the prior implementation tried
to do this via `_fields_and_field_types`, which doesn't exist on
ROS message classes — the actual introspection method is
`get_fields_and_field_types()`. Fixed here.
"""

import re
from typing import Any

from ._common import header_fields


_KEY_SANITIZE = re.compile(r'[^a-z0-9_]+')


def _sanitize_key(key: str) -> str:
    """Map a KeyValue key string to a SQL-safe lowercase column name."""
    s = _KEY_SANITIZE.sub('_', key.strip().lower()).strip('_')
    return s or 'unknown'


def extract(msg) -> dict[str, Any]:
    """Flatten Heartbeat: header + scalar fields + KeyValue payload."""
    fields: dict[str, Any] = {}
    if hasattr(msg, 'header'):
        fields.update(header_fields(msg.header))
    if hasattr(msg, 'get_fields_and_field_types'):
        for field_name in msg.get_fields_and_field_types():
            if field_name in ('header', 'values'):
                continue
            value = getattr(msg, field_name)
            if isinstance(value, (bool, int, float, str, bytes)):
                fields[field_name] = value
    if hasattr(msg, 'values'):
        for kv in msg.values:
            fields[_sanitize_key(kv.key)] = kv.value
    return fields
