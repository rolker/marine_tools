"""Shared helpers used by message-specific extractors."""

from __future__ import annotations

from typing import Any


def stamp_to_ns(stamp) -> int:
    """builtin_interfaces/Time -> int nanoseconds since epoch."""
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def header_fields(header) -> dict[str, Any]:
    """
    Return frame_id + header_t_ns for a std_msgs/Header.

    ``header_t_ns`` is the publisher-stamp time, distinct from the
    bag-receive timestamp that lands in the row's ``t_ns`` column.
    """
    return {
        'frame_id': header.frame_id,
        'header_t_ns': stamp_to_ns(header.stamp),
    }


def vector3_fields(v, prefix: str) -> dict[str, Any]:
    """geometry_msgs/Vector3 -> {prefix_x, prefix_y, prefix_z}."""
    return {f'{prefix}_x': v.x, f'{prefix}_y': v.y, f'{prefix}_z': v.z}


def point_fields(p, prefix: str) -> dict[str, Any]:
    """geometry_msgs/Point -> {prefix_x, prefix_y, prefix_z}."""
    return {f'{prefix}_x': p.x, f'{prefix}_y': p.y, f'{prefix}_z': p.z}


def quaternion_fields(q, prefix: str) -> dict[str, Any]:
    """geometry_msgs/Quaternion -> {prefix_x, prefix_y, prefix_z, prefix_w}."""
    return {
        f'{prefix}_x': q.x,
        f'{prefix}_y': q.y,
        f'{prefix}_z': q.z,
        f'{prefix}_w': q.w,
    }
