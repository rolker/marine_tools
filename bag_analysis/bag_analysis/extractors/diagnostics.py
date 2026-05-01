"""
diagnostic_msgs/DiagnosticArray extractor.

Per-message summary: counts by level plus a pipe-joined list of names
in WARN or ERROR state. This intentionally drops the per-status
key/value pairs — for Tier-1 sensor-health the question is "did
anything go red and which thing", not the full payload.
"""

from typing import Any

from ._common import header_fields


# diagnostic_msgs/DiagnosticStatus level constants (kept inline so
# this module doesn't need to import the message class to get them).
LEVEL_OK = 0
LEVEL_WARN = 1
LEVEL_ERROR = 2
LEVEL_STALE = 3


def _level_int(level: Any) -> int:
    r"""
    Coerce a DiagnosticStatus.level value to a plain int.

    rclpy delivers ROS ``byte`` fields as Python ``bytes`` of length 1,
    not as ``int``. Comparing ``b'\x01' == 1`` is silently False, so
    the level constants below never matched and every count came out as
    zero on real bag data. Normalise once up-front.
    """
    if isinstance(level, (bytes, bytearray)):
        return level[0] if level else 0
    return int(level)


def extract(msg) -> dict[str, Any]:
    """Aggregate a DiagnosticArray into per-level counts + flagged-name lists."""
    statuses = list(msg.status)
    levels = [_level_int(s.level) for s in statuses]
    error_names = [
        s.name for s, lvl in zip(statuses, levels) if lvl == LEVEL_ERROR
    ]
    warn_names = [
        s.name for s, lvl in zip(statuses, levels) if lvl == LEVEL_WARN
    ]
    return {
        **header_fields(msg.header),
        'n_status': len(statuses),
        'n_ok': levels.count(LEVEL_OK),
        'n_warn': levels.count(LEVEL_WARN),
        'n_error': levels.count(LEVEL_ERROR),
        'n_stale': levels.count(LEVEL_STALE),
        'error_names': '|'.join(error_names),
        'warn_names': '|'.join(warn_names),
    }
