"""mavros_msgs/State extractor."""

from typing import Any

from ._common import header_fields


def extract(msg) -> dict[str, Any]:
    """Flatten mavros_msgs/State to scalar columns."""
    return {
        **header_fields(msg.header),
        'connected': msg.connected,
        'armed': msg.armed,
        'guided': msg.guided,
        'manual_input': msg.manual_input,
        'mode': msg.mode,
        'system_status': msg.system_status,
    }
