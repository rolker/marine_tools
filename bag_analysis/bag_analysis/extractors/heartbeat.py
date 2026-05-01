"""
marine_interfaces/Heartbeat extractor.

The exact Heartbeat schema varies across marine_interfaces versions, so
this extractor introspects scalar fields rather than hard-coding them —
new fields land in the SQLite extract automatically and renames don't
break extraction.
"""

from typing import Any

from ._common import header_fields


def extract(msg) -> dict[str, Any]:
    """Flatten Heartbeat: header (if present) plus every scalar field."""
    fields: dict[str, Any] = {}
    if hasattr(msg, 'header'):
        fields.update(header_fields(msg.header))
    for field_name in getattr(msg, '_fields_and_field_types', {}):
        if field_name == 'header':
            continue
        value = getattr(msg, field_name)
        if isinstance(value, (bool, int, float, str, bytes)):
            fields[field_name] = value
    return fields
