"""
Tests for launch_recovery.detect() — the in-water-window detector.

Builds tiny SQLite extracts by hand (no rosbag2 round trip) with
altitude series shaped like crane launches:

    [crane top: ~3 m] → [in-water: ~0 m, jittery] → [crane top: ~3 m]

The detector should return the in-water window as the longest run of
samples within IN_WATER_THRESHOLD_M (2 m) of the smoothed minimum,
when that run exceeds MIN_IN_WATER_SECS (60 s).

Source-selection focus: when more than one altitude source is present
in the DB, the longest detected window across sources must win — the
candidate ordering in _candidate_altitude_tables is a tie-breaker
only, NOT a hard preference that lets a partial track truncate the
deployment span.
"""

import sqlite3
from typing import Iterable

from bag_analysis.launch_recovery import detect


_START_NS = 1_700_000_000_000_000_000
_NS_PER_S = 1_000_000_000


def _make_altitude_series(
    *,
    pre_in_water_s: int,
    in_water_s: int,
    post_in_water_s: int,
    crane_alt_m: float = 3.0,
    water_alt_m: float = 0.0,
) -> list[tuple[int, float]]:
    """Build a 1-Hz (t_ns, altitude) sequence: crane | in-water | crane."""
    rows: list[tuple[int, float]] = []
    t = _START_NS
    for _ in range(pre_in_water_s):
        rows.append((t, crane_alt_m))
        t += _NS_PER_S
    for _ in range(in_water_s):
        rows.append((t, water_alt_m))
        t += _NS_PER_S
    for _ in range(post_in_water_s):
        rows.append((t, crane_alt_m))
        t += _NS_PER_S
    return rows


def _open_conn(tmp_path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / 'extract.sqlite')


def _create_alt_table(
    conn: sqlite3.Connection, table: str, rows: Iterable[tuple[int, float]],
) -> None:
    conn.execute(f'CREATE TABLE "{table}" (t_ns INTEGER, altitude REAL)')
    conn.executemany(
        f'INSERT INTO "{table}" (t_ns, altitude) VALUES (?, ?)', list(rows),
    )


_SBG = 't_bizzy_sensors_sbg_ekf_nav'
_MAVROS = 't_bizzy_mavros_global_position_raw_fix'


# --------------------------------------------------------------- happy path

def test_detect_uses_only_available_source(tmp_path):
    """Single source present, plausible window → that window is returned."""
    conn = _open_conn(tmp_path)
    _create_alt_table(
        conn, _MAVROS,
        _make_altitude_series(
            pre_in_water_s=30, in_water_s=600, post_in_water_s=30,
        ),
    )
    conn.commit()
    launch_ns, recovery_ns = detect(conn, 'bizzy')
    assert launch_ns is not None and recovery_ns is not None
    # Detected window should span roughly the in-water region.
    assert (recovery_ns - launch_ns) // _NS_PER_S >= 500


# --------------------------------------------------------------- key regression: source-selection

def test_detect_prefers_longer_run_when_sbg_partial(tmp_path):
    """Partial SBG must not truncate the window when mavros covers full bag."""
    conn = _open_conn(tmp_path)
    # SBG: short coverage, all in-water — yields a ~120 s window
    _create_alt_table(
        conn, _SBG,
        _make_altitude_series(
            pre_in_water_s=0, in_water_s=120, post_in_water_s=0,
        ),
    )
    # mavros: full deployment, ~1000 s in-water window
    _create_alt_table(
        conn, _MAVROS,
        _make_altitude_series(
            pre_in_water_s=30, in_water_s=1000, post_in_water_s=30,
        ),
    )
    conn.commit()
    launch_ns, recovery_ns = detect(conn, 'bizzy')
    assert launch_ns is not None and recovery_ns is not None
    detected_s = (recovery_ns - launch_ns) // _NS_PER_S
    # Must be the mavros window (~1000 s), not the SBG one (~120 s).
    assert detected_s >= 800, (
        f'expected mavros (long) window, got {detected_s} s — '
        'detector likely returned SBG result on first hit'
    )


def test_detect_ties_break_to_preferred_source(tmp_path):
    """Tied run lengths break to the preferred (SBG-first) source."""
    conn = _open_conn(tmp_path)
    rows = _make_altitude_series(
        pre_in_water_s=30, in_water_s=600, post_in_water_s=30,
    )
    # Offset mavros by 2 hours so the two windows are clearly distinct.
    offset_ns = 2 * 3600 * _NS_PER_S
    _create_alt_table(conn, _SBG, rows)
    _create_alt_table(
        conn, _MAVROS, [(t + offset_ns, a) for t, a in rows],
    )
    conn.commit()
    launch_ns, recovery_ns = detect(conn, 'bizzy')
    assert launch_ns is not None and recovery_ns is not None
    # SBG window starts near _START_NS; mavros would start ~2 h later.
    assert launch_ns < _START_NS + 3600 * _NS_PER_S, (
        'preferred SBG source should win on tied run length'
    )


# --------------------------------------------------------------- empty / missing cases

def test_detect_returns_none_when_no_table(tmp_path):
    """No altitude tables present → (None, None)."""
    conn = _open_conn(tmp_path)
    conn.commit()
    assert detect(conn, 'bizzy') == (None, None)


def test_detect_returns_none_when_table_empty(tmp_path):
    """Altitude table exists but holds no rows → (None, None)."""
    conn = _open_conn(tmp_path)
    _create_alt_table(conn, _SBG, [])
    conn.commit()
    assert detect(conn, 'bizzy') == (None, None)


def test_detect_falls_through_when_run_too_short(tmp_path):
    """Sub-MIN_IN_WATER_SECS run on SBG falls through to plausible mavros."""
    conn = _open_conn(tmp_path)
    # SBG: too-short in-water window (10 s, below MIN_IN_WATER_SECS=60)
    _create_alt_table(
        conn, _SBG,
        _make_altitude_series(
            pre_in_water_s=10, in_water_s=10, post_in_water_s=10,
        ),
    )
    # mavros: plausible window
    _create_alt_table(
        conn, _MAVROS,
        _make_altitude_series(
            pre_in_water_s=30, in_water_s=600, post_in_water_s=30,
        ),
    )
    conn.commit()
    launch_ns, recovery_ns = detect(conn, 'bizzy')
    assert launch_ns is not None and recovery_ns is not None
    assert (recovery_ns - launch_ns) // _NS_PER_S >= 500
