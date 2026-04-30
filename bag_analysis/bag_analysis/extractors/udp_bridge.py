"""udp_bridge_interfaces extractors.

Schemas in udp_bridge_interfaces have shifted across versions, so both
extractors here introspect rather than hard-code. BridgeInfo is a flat
struct so we pull every scalar; TopicStatisticsArray is a list-of-stats
which we summarize per message — the Tier-1 comms plot needs totals
over time, not the full per-topic breakdown.
"""

from typing import Any


def extract_bridge_info(msg) -> dict[str, Any]:
    """Flatten BridgeInfo's scalar fields via introspection."""
    fields: dict[str, Any] = {}
    for field_name in getattr(msg, '_fields_and_field_types', {}):
        value = getattr(msg, field_name)
        if isinstance(value, (bool, int, float, str)):
            fields[field_name] = value
    return fields


def extract_topic_statistics_array(msg) -> dict[str, Any]:
    """Aggregate the per-topic stats array into per-message totals."""
    # Field is `topics` in some versions, `statistics` in others.
    stats = list(
        getattr(msg, 'topics', None)
        or getattr(msg, 'statistics', None)
        or []
    )
    if not stats:
        return {'n_topics': 0}

    msgs_in = sum(getattr(s, 'messages_received', 0) for s in stats)
    bytes_in = sum(getattr(s, 'bytes_received', 0) for s in stats)
    msgs_out = sum(getattr(s, 'messages_sent', 0) for s in stats)
    bytes_out = sum(getattr(s, 'bytes_sent', 0) for s in stats)
    drops = sum(getattr(s, 'drops', 0) for s in stats)
    return {
        'n_topics': len(stats),
        'total_messages_received': msgs_in,
        'total_bytes_received': bytes_in,
        'total_messages_sent': msgs_out,
        'total_bytes_sent': bytes_out,
        'total_drops': drops,
    }
