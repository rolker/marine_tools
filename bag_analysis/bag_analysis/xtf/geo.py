"""
WGS84 geodesy helpers for the XTF exporter.

Turns an ``earth`` (ECEF) TF lookup into the geographic position and
attitude an XTF ping header needs.

The ROS ``earth`` frame is the Earth-Centred Earth-Fixed (ECEF) frame
(REP-105), so a ``lookup_transform('earth', sensor_frame)`` yields the
sensor's ECEF position (translation) and its orientation expressed in
ECEF. XTF wants geographic coordinates (decimal degrees) plus
heading/pitch/roll relative to local north. This module does that
conversion with no third-party dependencies (pure ``numpy`` + ``math``),
so the converter inherits no runtime deps beyond what ``bag_analysis``
already declares.

All angles are radians inside the functions; the public ping-facing
helpers return degrees where noted.
"""

from __future__ import annotations

import math

import numpy as np

# WGS84 ellipsoid constants.
_WGS84_A = 6378137.0                     # semi-major axis (m)
_WGS84_F = 1.0 / 298.257223563           # flattening
_WGS84_B = _WGS84_A * (1.0 - _WGS84_F)   # semi-minor axis (m)
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)  # first eccentricity squared


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> tuple[
        float, float, float]:
    """Convert geodetic lat/lon/alt (WGS84) to ECEF X/Y/Z metres."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    n = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
    x = (n + alt_m) * math.cos(lat) * math.cos(lon)
    y = (n + alt_m) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - _WGS84_E2) + alt_m) * sin_lat
    return x, y, z


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[
        float, float, float]:
    """
    Convert ECEF X/Y/Z metres to geodetic lat/lon (degrees) and alt (m).

    Uses Bowring's iteration, which converges in a few steps for any
    point on or near the Earth's surface.
    """
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p < 1e-9:
        # On the polar axis; latitude is +/-90 deg, longitude undefined.
        lat = math.copysign(math.pi / 2.0, z)
        alt = abs(z) - _WGS84_B
        return math.degrees(lat), math.degrees(lon), alt

    lat = math.atan2(z, p * (1.0 - _WGS84_E2))
    for _ in range(8):
        sin_lat = math.sin(lat)
        n = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
        lat = math.atan2(z + _WGS84_E2 * n * sin_lat, p)
    sin_lat = math.sin(lat)
    n = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
    alt = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), alt


def ecef_to_enu_rotation(lat_deg: float, lon_deg: float) -> np.ndarray:
    """
    Return the 3x3 rotation mapping an ECEF vector to local ENU.

    Columns of the result expressed as a matrix multiply: ``v_enu = R @
    v_ecef``. ENU axes are East, North, Up at the given geodetic point.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    return np.array([
        [-sin_lon, cos_lon, 0.0],
        [-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat],
        [cos_lat * cos_lon, cos_lat * sin_lon, sin_lat],
    ])


# ENU (East, North, Up) -> NED (North, East, Down).
_ENU_TO_NED = np.array([
    [0.0, 1.0, 0.0],
    [1.0, 0.0, 0.0],
    [0.0, 0.0, -1.0],
])


def quaternion_to_matrix(x: float, y: float, z: float, w: float) -> np.ndarray:
    """
    Convert a quaternion (x, y, z, w) to a 3x3 rotation matrix.

    The matrix maps a vector in the rotated (body) frame to the
    reference frame, matching the tf2 convention where
    ``p_parent = R @ p_child``.
    """
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm < 1e-12:
        return np.eye(3)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def matrix_to_heading_pitch_roll(r_body_to_ned: np.ndarray) -> tuple[
        float, float, float]:
    """
    Extract (heading, pitch, roll) in degrees from a body->NED matrix.

    Uses the aerospace ZYX (yaw-pitch-roll) convention. Heading is
    returned in ``[0, 360)`` degrees clockwise from north; pitch and
    roll in ``[-180, 180]``.
    """
    heading = math.atan2(r_body_to_ned[1, 0], r_body_to_ned[0, 0])
    pitch = math.atan2(
        -r_body_to_ned[2, 0],
        math.hypot(r_body_to_ned[2, 1], r_body_to_ned[2, 2]),
    )
    roll = math.atan2(r_body_to_ned[2, 1], r_body_to_ned[2, 2])
    return math.degrees(heading) % 360.0, math.degrees(pitch), \
        math.degrees(roll)


def ecef_pose_to_geo(
    translation: tuple[float, float, float],
    quaternion: tuple[float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    """
    Convert an ``earth``->sensor TF pose to geographic position + attitude.

    Parameters
    ----------
    translation
        ECEF (x, y, z) of the sensor origin, metres.
    quaternion
        Sensor orientation in ECEF as (x, y, z, w), tf2 convention.

    Returns
    -------
    tuple
        ``(lat_deg, lon_deg, alt_m, heading_deg, pitch_deg, roll_deg)``.
        Heading/pitch/roll are the sensor body axes relative to local
        NED at the sensor's position.

    """
    x, y, z = translation
    lat_deg, lon_deg, alt_m = ecef_to_geodetic(x, y, z)
    r_body_to_ecef = quaternion_to_matrix(*quaternion)
    r_ecef_to_enu = ecef_to_enu_rotation(lat_deg, lon_deg)
    r_body_to_ned = _ENU_TO_NED @ r_ecef_to_enu @ r_body_to_ecef
    heading, pitch, roll = matrix_to_heading_pitch_roll(r_body_to_ned)
    return lat_deg, lon_deg, alt_m, heading, pitch, roll
