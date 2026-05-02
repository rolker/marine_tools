"""nav_msgs/Odometry extractor."""

from typing import Any

from ._common import (
    header_fields,
    point_fields,
    quaternion_fields,
    vector3_fields,
)


def extract(msg) -> dict[str, Any]:
    """Flatten Odometry: pose + twist; covariance arrays skipped."""
    return {
        **header_fields(msg.header),
        'child_frame_id': msg.child_frame_id,
        **point_fields(msg.pose.pose.position, 'pos'),
        **quaternion_fields(msg.pose.pose.orientation, 'q'),
        **vector3_fields(msg.twist.twist.linear, 'vel'),
        **vector3_fields(msg.twist.twist.angular, 'omega'),
    }
