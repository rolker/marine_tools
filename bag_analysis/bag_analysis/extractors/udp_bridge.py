"""
udp_bridge_interfaces extractors.

Both extractors aggregate their nested structure into a single flat row
per message. The schemas are wide and lists are nested two levels deep,
so emitting per-(remote, connection) rows would require multi-row
inserts the writer doesn't currently support — Tier-1 wants totals over
time anyway. A future enhancement could split by physical link.

Wire-bytes accounting (BridgeInfo): the bandwidth that actually traverses
the link is ``Σ {message, overhead, resend}.{success, failed}_bytes_per_second``
across all connections — ``dropped`` is excluded because the rate-limiter
never let those bytes hit the wire, and ``received_bytes_per_second`` is
captured separately for the BOAT-IN side.
"""

from typing import Any


def extract_bridge_info(msg) -> dict[str, Any]:
    """Flatten BridgeInfo: top-level scalars + bandwidth totals."""
    fields: dict[str, Any] = {}
    for field_name in getattr(msg, '_fields_and_field_types', {}):
        value = getattr(msg, field_name)
        if isinstance(value, (bool, int, float, str)):
            fields[field_name] = value

    received_bps = 0.0
    duplicate_bps = 0.0
    wire_out_bps = 0.0
    n_remotes = 0
    n_connections = 0

    for remote in getattr(msg, 'remotes', []) or []:
        n_remotes += 1
        for conn in getattr(remote, 'connections', []) or []:
            n_connections += 1
            received_bps += float(
                getattr(conn, 'received_bytes_per_second', 0.0),
            )
            duplicate_bps += float(
                getattr(conn, 'duplicate_bytes_per_second', 0.0),
            )
            for sub_name in ('message', 'overhead', 'resend'):
                sub = getattr(conn, sub_name, None)
                if sub is None:
                    continue
                wire_out_bps += float(
                    getattr(sub, 'success_bytes_per_second', 0.0),
                )
                wire_out_bps += float(
                    getattr(sub, 'failed_bytes_per_second', 0.0),
                )

    fields.update({
        'n_remotes': n_remotes,
        'n_connections': n_connections,
        'received_bytes_per_second': received_bps,
        'duplicate_bytes_per_second': duplicate_bps,
        'wire_out_bytes_per_second': wire_out_bps,
    })
    return fields


def extract_topic_statistics_array(msg) -> dict[str, Any]:
    """Aggregate per-topic stats into per-message totals."""
    # Field is `topics` in current versions, `statistics` in older ones.
    stats = list(
        getattr(msg, 'topics', None)
        or getattr(msg, 'statistics', None)
        or []
    )
    if not stats:
        return {'n_topics': 0}

    msg_bytes_per_s = sum(
        float(getattr(s, 'message_bytes_per_second', 0.0)) for s in stats
    )
    msgs_per_s = sum(
        float(getattr(s, 'messages_per_second', 0.0)) for s in stats
    )

    success_bps = 0.0
    failed_bps = 0.0
    dropped_bps = 0.0
    for s in stats:
        send = getattr(s, 'send', None)
        if send is None:
            continue
        success_bps += float(getattr(send, 'success_bytes_per_second', 0.0))
        failed_bps += float(getattr(send, 'failed_bytes_per_second', 0.0))
        dropped_bps += float(getattr(send, 'dropped_bytes_per_second', 0.0))

    return {
        'n_topics': len(stats),
        'message_bytes_per_second': msg_bytes_per_s,
        'messages_per_second': msgs_per_s,
        'send_success_bytes_per_second': success_bps,
        'send_failed_bytes_per_second': failed_bps,
        'send_dropped_bytes_per_second': dropped_bps,
    }
