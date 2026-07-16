# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Unit tests for the pure angular-response CSV loader (marine_tools#71).

The loader must mirror ``cube::loadAngularResponseCurveWithHeader``
(cube_bathymetry) exactly; these cases track that function's documented
semantics, including std::stof leading-prefix parsing.
"""

from kongsberg_em_bridge.angular_response import load_angular_response_curve


def _write(tmp_path, text):
    path = tmp_path / 'curve.csv'
    path.write_text(text)
    return str(path)


TIER1_CSV = """abs_angle_deg_center,mean_bs_db,n,db_relative_to_nadir
0.5,-20.0,100,0.0
10.5,-24.0,90,-4.0
30.5,-32.0,80,-12.0
"""

TIER2_CSV = """# derived by derive_angular_response.py
# tl_removed: true
# absorption_db_per_m: 0.00025
abs_angle_deg_center,mean_bs_db,n,db_relative_to_nadir
0.5,-20.0,100,0.0
30.5,-32.0,80,-12.0
"""


def test_tier1_roundtrip(tmp_path):
    points, tl_removed, alpha = load_angular_response_curve(
        _write(tmp_path, TIER1_CSV))
    assert points == [(0.5, 0.0), (10.5, -4.0), (30.5, -12.0)]
    assert tl_removed is False
    assert alpha == 0.0


def test_tier2_provenance_header(tmp_path):
    points, tl_removed, alpha = load_angular_response_curve(
        _write(tmp_path, TIER2_CSV))
    assert len(points) == 2
    assert tl_removed is True
    assert abs(alpha - 0.00025) < 1e-9


def test_tl_removed_value_variants(tmp_path):
    # Case-insensitive true / 1 => True; anything else => False (C++ parity).
    for value, expected in [('true', True), ('TRUE', True), ('1', True),
                            ('false', False), ('yes', False), ('', False)]:
        path = _write(tmp_path, f'# tl_removed: {value}\n0.0,-20.0,1,0.0\n')
        _, tl_removed, _ = load_angular_response_curve(path)
        assert tl_removed is expected, value


def test_malformed_absorption_keeps_default(tmp_path):
    path = _write(tmp_path, '# tl_removed: true\n'
                            '# absorption_db_per_m: banana\n'
                            '0.0,-20.0,1,0.0\n')
    _, tl_removed, alpha = load_angular_response_curve(path)
    assert tl_removed is True
    assert alpha == 0.0


def test_skips_malformed_rows_and_sorts(tmp_path):
    path = _write(tmp_path, 'abs_angle_deg_center,mean_bs_db,n,db\n'
                            '30.5,-32.0,80,-12.0\n'
                            'not,a,row,here\n'
                            'short,row\n'
                            '\n'
                            '0.5,-20.0,100,0.0\n')
    points, _, _ = load_angular_response_curve(path)
    assert points == [(0.5, 0.0), (30.5, -12.0)]  # sorted ascending


def test_stof_leading_prefix_tolerance(tmp_path):
    # "1deg" parses its numeric prefix (C++ std::stof parity); a field with
    # no leading number is rejected.
    path = _write(tmp_path, '1deg,-20.0,1, -4.0dB\ndeg1,-20.0,1,0.0\n')
    points, _, _ = load_angular_response_curve(path)
    assert points == [(1.0, -4.0)]


def test_missing_and_empty_path():
    assert load_angular_response_curve('') == ([], False, 0.0)
    assert load_angular_response_curve('/nonexistent/nope.csv') \
        == ([], False, 0.0)
