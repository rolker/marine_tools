"""sensor_msgs/Imu extractor."""

from typing import Any

from ._common import header_fields, quaternion_fields, vector3_fields


def extract(msg) -> dict[str, Any]:
    """Flatten Imu: orientation quaternion + angular_velocity + linear_accel."""
    return {
        **header_fields(msg.header),
        **quaternion_fields(msg.orientation, 'q'),
        **vector3_fields(msg.angular_velocity, 'omega'),
        **vector3_fields(msg.linear_acceleration, 'accel'),
    }
