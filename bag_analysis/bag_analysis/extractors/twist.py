"""geometry_msgs/TwistStamped extractor."""

from typing import Any

from ._common import header_fields, vector3_fields


def extract(msg) -> dict[str, Any]:
    """Flatten TwistStamped to header + linear/angular velocity scalars."""
    return {
        **header_fields(msg.header),
        **vector3_fields(msg.twist.linear, 'vel'),
        **vector3_fields(msg.twist.angular, 'omega'),
    }
