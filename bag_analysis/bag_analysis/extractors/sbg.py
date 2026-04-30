"""sbg_driver message extractors.

Pull scalar fields from the various sbg_driver messages. Each message
has a `status` sub-struct with bit-fields; we extract the numeric
`type` byte (the GPS fix grade or EKF solution mode) but leave the
deeper bit-by-bit decode for downstream tooling — Tier-1 plots only
need fix grade, not the full enum.

`getattr(..., default)` is used on a few fields that have come and
gone across sbg_driver versions; the goal is "extract whatever this
firmware emits", not "fail on a renamed field."
"""

from typing import Any

from ._common import header_fields


def extract_gps_pos(msg) -> dict[str, Any]:
    """sbg_driver/SbgGpsPos -> lat/lon/alt + fix grade."""
    return {
        **header_fields(msg.header),
        'time_stamp': msg.time_stamp,
        'gps_tow': msg.gps_tow,
        'latitude': msg.latitude,
        'longitude': msg.longitude,
        'altitude': msg.altitude,
        'undulation': msg.undulation,
        'num_sv_used': msg.num_sv_used,
        'base_station_id': getattr(msg, 'base_station_id', 0),
        'diff_age': getattr(msg, 'diff_age', 0),
        'status_type': getattr(msg.status, 'type', -1),
    }


def extract_gps_vel(msg) -> dict[str, Any]:
    """sbg_driver/SbgGpsVel -> NED velocity + course over ground."""
    return {
        **header_fields(msg.header),
        'time_stamp': msg.time_stamp,
        'gps_tow': msg.gps_tow,
        'velocity_n': msg.velocity.x,
        'velocity_e': msg.velocity.y,
        'velocity_d': msg.velocity.z,
        'course': msg.course,
        'course_acc': getattr(msg, 'course_acc', float('nan')),
        'status_type': getattr(msg.status, 'type', -1),
    }


def extract_ekf_nav(msg) -> dict[str, Any]:
    """sbg_driver/SbgEkfNav -> EKF-fused position + velocity + solution mode."""
    return {
        **header_fields(msg.header),
        'time_stamp': msg.time_stamp,
        'velocity_n': msg.velocity.x,
        'velocity_e': msg.velocity.y,
        'velocity_d': msg.velocity.z,
        'latitude': msg.position.x,
        'longitude': msg.position.y,
        'altitude': msg.position.z,
        'undulation': msg.undulation,
        'solution_mode': getattr(msg.status, 'solution_mode', -1),
    }


def extract_gps_hdt(msg) -> dict[str, Any]:
    """sbg_driver/SbgGpsHdt -> dual-antenna heading."""
    return {
        **header_fields(msg.header),
        'time_stamp': msg.time_stamp,
        'tow': msg.tow,
        'true_heading': msg.true_heading,
        'true_heading_acc': msg.true_heading_acc,
        'pitch': msg.pitch,
        'pitch_acc': msg.pitch_acc,
        'baseline': msg.baseline,
        'num_sv_tracked': msg.num_sv_tracked,
        'num_sv_used': msg.num_sv_used,
        'status_type': getattr(msg.status, 'type', -1),
    }


def extract_status(msg) -> dict[str, Any]:
    """sbg_driver/SbgStatus -> raw status words for downstream bitfield decode."""
    return {
        **header_fields(msg.header),
        'time_stamp': msg.time_stamp,
        'status_general': getattr(msg, 'status_general', 0),
        'status_com': getattr(msg, 'status_com', 0),
        'status_aiding': getattr(msg, 'status_aiding', 0),
    }
