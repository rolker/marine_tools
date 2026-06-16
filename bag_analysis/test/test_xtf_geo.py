"""Tests for bag_analysis.xtf.geo WGS84 / attitude helpers."""

import math

from bag_analysis.xtf.geo import (
    ecef_pose_to_geo,
    ecef_to_enu_rotation,
    ecef_to_geodetic,
    geodetic_to_ecef,
    matrix_to_heading_pitch_roll,
)
import numpy as np

# Lake Massabesic, where the 2026-06-15 sidescan was captured.
_LAT, _LON, _ALT = 42.990559, -71.392957, 48.5


def test_geodetic_ecef_round_trip():
    x, y, z = geodetic_to_ecef(_LAT, _LON, _ALT)
    lat, lon, alt = ecef_to_geodetic(x, y, z)
    assert math.isclose(lat, _LAT, abs_tol=1e-7)
    assert math.isclose(lon, _LON, abs_tol=1e-7)
    assert math.isclose(alt, _ALT, abs_tol=1e-3)


def test_geodetic_to_ecef_known_origin():
    # (0, 0, 0) sits on the equator at the prime meridian: +X axis.
    x, y, z = geodetic_to_ecef(0.0, 0.0, 0.0)
    assert math.isclose(x, 6378137.0, abs_tol=1e-3)
    assert math.isclose(y, 0.0, abs_tol=1e-6)
    assert math.isclose(z, 0.0, abs_tol=1e-6)


def test_ecef_to_enu_rotation_at_origin():
    # At lat=lon=0: ECEF +X->Up, +Y->East, +Z->North.
    r = ecef_to_enu_rotation(0.0, 0.0)
    assert np.allclose(r @ np.array([1.0, 0, 0]), [0, 0, 1], atol=1e-9)  # Up
    assert np.allclose(r @ np.array([0, 1.0, 0]), [1, 0, 0], atol=1e-9)  # East
    assert np.allclose(r @ np.array([0, 0, 1.0]), [0, 1, 0], atol=1e-9)  # N


def test_heading_pitch_roll_from_identity():
    heading, pitch, roll = matrix_to_heading_pitch_roll(np.eye(3))
    assert math.isclose(heading, 0.0, abs_tol=1e-9)
    assert math.isclose(pitch, 0.0, abs_tol=1e-9)
    assert math.isclose(roll, 0.0, abs_tol=1e-9)


def test_heading_pitch_roll_pure_yaw():
    # 90 deg yaw about NED down axis -> heading 90 (facing east).
    c, s = math.cos(math.pi / 2), math.sin(math.pi / 2)
    r = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    heading, pitch, roll = matrix_to_heading_pitch_roll(r)
    assert math.isclose(heading, 90.0, abs_tol=1e-6)
    assert math.isclose(pitch, 0.0, abs_tol=1e-6)
    assert math.isclose(roll, 0.0, abs_tol=1e-6)


def _matrix_to_quaternion(r: np.ndarray):
    """Convert a rotation matrix to (x, y, z, w) for test construction."""
    w = math.sqrt(max(0.0, 1.0 + r[0, 0] + r[1, 1] + r[2, 2])) / 2.0
    x = math.sqrt(max(0.0, 1.0 + r[0, 0] - r[1, 1] - r[2, 2])) / 2.0
    y = math.sqrt(max(0.0, 1.0 - r[0, 0] + r[1, 1] - r[2, 2])) / 2.0
    z = math.sqrt(max(0.0, 1.0 - r[0, 0] - r[1, 1] + r[2, 2])) / 2.0
    x = math.copysign(x, r[2, 1] - r[1, 2])
    y = math.copysign(y, r[0, 2] - r[2, 0])
    z = math.copysign(z, r[1, 0] - r[0, 1])
    return x, y, z, w


def test_ecef_pose_to_geo_north_aligned():
    # Build a sensor whose body axes coincide with local NED (heading 0).
    # body->ECEF = (ENU->NED @ ECEF->ENU)^T when body->NED is identity.
    enu_to_ned = np.array([[0, 1.0, 0], [1.0, 0, 0], [0, 0, -1.0]])
    r_ecef_to_enu = ecef_to_enu_rotation(_LAT, _LON)
    r_body_to_ecef = (enu_to_ned @ r_ecef_to_enu).T
    quat = _matrix_to_quaternion(r_body_to_ecef)
    translation = geodetic_to_ecef(_LAT, _LON, _ALT)

    lat, lon, alt, heading, pitch, roll = ecef_pose_to_geo(translation, quat)
    assert math.isclose(lat, _LAT, abs_tol=1e-6)
    assert math.isclose(lon, _LON, abs_tol=1e-6)
    assert math.isclose(alt, _ALT, abs_tol=1e-2)
    # Heading is on a circle: 0 and 360 are the same bearing.
    assert abs((heading + 180.0) % 360.0 - 180.0) < 1e-4
    assert math.isclose(pitch, 0.0, abs_tol=1e-4)
    assert math.isclose(roll, 0.0, abs_tol=1e-4)
