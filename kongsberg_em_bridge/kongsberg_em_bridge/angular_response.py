# Copyright 2026 Center for Coastal and Ocean Mapping & NOAA-UNH Joint
# Hydrographic Center, University of New Hampshire
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Pure-Python loader for empirical angular-response curve CSVs (marine_tools#71).

Mirrors ``cube::loadAngularResponseCurveWithHeader``
(``cube_bathymetry/src/angular_response_curve.cpp``) exactly, so a curve file
produced by ``cube_bathymetry/scripts/derive_angular_response.py`` yields the
same points and TL provenance whether loaded by the CUBE estimator (C++) or
published in ``SonarInfo`` by this driver. Framework-free like
``em_datagrams`` so it is unit-tested directly.
"""

import re
from typing import List, Optional, Tuple

# std::stof semantics: parse a leading numeric prefix, tolerate trailing
# garbage ("1deg" -> 1.0), reject fields with no leading number (so the CSV
# header row "abs_angle_deg_center,..." is skipped by the same rule as C++).
_LEADING_FLOAT = re.compile(
    r'^[ \t]*[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?')


def _leading_float(text: str) -> Optional[float]:
    """Parse a leading float like ``std::stof``; None when there is none."""
    match = _LEADING_FLOAT.match(text)
    if match is None:
        return None
    return float(match.group(0))


def _header_comment_value(line: str, key: str) -> Optional[str]:
    """
    Return the trimmed value of a ``# key: value`` provenance comment.

    ``line`` is known to start (after whitespace) with ``#``. Tolerant of
    arbitrary spacing around the colon, like the C++ ``matchHeaderComment``.
    Returns None when the line is not a ``key: ...`` comment.
    """
    body = line[line.index('#') + 1:]
    colon = body.find(':')
    if colon < 0:
        return None
    if body[:colon].strip() != key:
        return None
    return body[colon + 1:].strip()


def load_angular_response_curve(
        path: str) -> Tuple[List[Tuple[float, float]], bool, float]:
    """
    Load a curve CSV; return ``(points, tl_removed, absorption_db_per_m)``.

    ``points`` is the ascending ``(abs_angle_deg, db_relative_to_nadir)``
    curve (columns 0 and 3 of ``abs_angle_deg_center,mean_bs_db,n,
    db_relative_to_nadir`` rows). The optional provenance comments
    ``# tl_removed: true|false`` and ``# absorption_db_per_m: <float>``
    (cube_bathymetry#87) yield the TL provenance; their absence gives the
    tier-1 defaults ``(False, 0.0)``. Comment lines, blank lines, the header
    row, and malformed rows/values are skipped. An empty path or a
    missing/unreadable file yields ``([], False, 0.0)`` -- the caller decides
    how to warn; loading is never fatal.
    """
    points: List[Tuple[float, float]] = []
    tl_removed = False
    absorption_db_per_m = 0.0
    if not path:
        return points, tl_removed, absorption_db_per_m
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError:
        return points, tl_removed, absorption_db_per_m

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#'):
            value = _header_comment_value(stripped, 'tl_removed')
            if value is not None:
                tl_removed = value.lower() in ('true', '1')
                continue
            value = _header_comment_value(stripped, 'absorption_db_per_m')
            if value is not None:
                alpha = _leading_float(value)
                if alpha is not None:
                    absorption_db_per_m = alpha
            continue
        fields = stripped.split(',')
        if len(fields) < 4:
            continue
        angle = _leading_float(fields[0])
        db_rel = _leading_float(fields[3])
        if angle is None or db_rel is None:
            continue
        points.append((angle, db_rel))

    points.sort(key=lambda p: p[0])
    return points, tl_removed, absorption_db_per_m
