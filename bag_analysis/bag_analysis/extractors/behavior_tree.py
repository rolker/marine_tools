"""nav2_msgs/BehaviorTreeLog extractor.

A BehaviorTreeLog message carries a list of state-change events. The
mode-timeline plot needs message-level granularity, not event-level —
so we summarize per message with the count of events plus the last
event's details (which captures the "current" BT state at this msg's
timestamp).
"""

from typing import Any

from ._common import stamp_to_ns


def extract(msg) -> dict[str, Any]:
    """Per-message summary: event count + last event's details."""
    events = list(getattr(msg, 'event_log', []) or [])
    fields: dict[str, Any] = {'n_events': len(events)}

    if events:
        last = events[-1]
        fields['last_node_name'] = getattr(last, 'node_name', '')
        fields['last_previous_status'] = getattr(last, 'previous_status', '')
        fields['last_current_status'] = getattr(last, 'current_status', '')
        ts = getattr(last, 'timestamp', None)
        if ts is not None and hasattr(ts, 'sec'):
            fields['last_event_t_ns'] = stamp_to_ns(ts)

    return fields
