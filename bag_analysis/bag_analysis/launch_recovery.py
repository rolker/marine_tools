"""
Launch / recovery detection for crane-launched marine vehicles.

Almost every downstream analysis ("max speed", "voltage sag under load",
etc.) implicitly assumes "while in the water." For crane-launched
vehicles like BizzyBoat, altitude is a clean signal: the boat sits a
few metres above water level on the crane, drops sharply at launch,
holds at water level (with wave-induced jitter) for the deployment,
then rises sharply at recovery.

Algorithm (deliberately simple — easy to revisit when ramp launches
or other deployment modes appear):

1. Pick an altitude source, preferring SBG EKF over mavros raw fix.
2. Resample to 1 Hz median, then 10 s rolling median (matches the
   altitude_heave plot's smoothing).
3. Find the minimum smoothed altitude. The *in-water* window is
   defined as samples within ``IN_WATER_THRESHOLD_M`` of that minimum.
4. Take the **longest contiguous run** of in-water samples; report
   its first/last timestamps as ``launch_t_ns`` / ``recovery_t_ns``.

Returns ``(None, None)`` when:
- No altitude source is present in the DB
- The altitude column is entirely NaN
- The detected window is implausibly short (< MIN_IN_WATER_SECS)

Future deployment modes (ramp, lake bank, dock side) will likely need
a different signal — speed-of-arming, contact sensor, or operator
annotation — but the *interface* (a ``launch_t_ns`` /
``recovery_t_ns`` pair stored in ``_bag_meta``) stays.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd


IN_WATER_THRESHOLD_M = 2.0
MIN_IN_WATER_SECS = 60
SMOOTHING_RESAMPLE = '1s'
SMOOTHING_ROLLING_WINDOW_S = 10


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def _candidate_altitude_tables(namespace: str) -> list[tuple[str, str]]:
    """Return [(table_name, altitude_column), ...] in preference order."""
    return [
        (f't_{namespace}_sensors_sbg_ekf_nav', 'altitude'),
        (f't_{namespace}_mavros_global_position_raw_fix', 'altitude'),
        (f't_{namespace}_mavros_global_position_global', 'altitude'),
    ]


def _detect_from_series(
    t_ns: pd.Series, altitude: pd.Series,
) -> tuple[Optional[int], Optional[int]]:
    """Run the altitude-window algorithm on a sorted (t_ns, altitude) pair."""
    if altitude.dropna().empty:
        return None, None

    t_dt = pd.to_datetime(t_ns, unit='ns')
    s = pd.Series(altitude.values, index=t_dt).sort_index()
    smoothed = (
        s.resample(SMOOTHING_RESAMPLE).median()
        .rolling(SMOOTHING_ROLLING_WINDOW_S, center=True, min_periods=1).median()
    )
    smoothed = smoothed.dropna()
    if smoothed.empty:
        return None, None

    min_alt = smoothed.min()
    in_water = smoothed < (min_alt + IN_WATER_THRESHOLD_M)

    # Group consecutive runs and find the longest True run.
    group_id = (in_water != in_water.shift()).cumsum()
    runs = in_water.groupby(group_id)
    longest_run_id = None
    longest_run_len = 0
    for gid, run in runs:
        if run.iloc[0] and len(run) > longest_run_len:
            longest_run_id = gid
            longest_run_len = len(run)

    if longest_run_id is None or longest_run_len < MIN_IN_WATER_SECS:
        return None, None

    longest = in_water[group_id == longest_run_id]
    launch_dt, recovery_dt = longest.index[0], longest.index[-1]
    return int(launch_dt.value), int(recovery_dt.value)


def detect(
    conn: sqlite3.Connection, namespace: str,
) -> tuple[Optional[int], Optional[int]]:
    """
    Detect the in-water window across all altitude data in the DB.

    Tries altitude sources in preference order (SBG EKF first, then
    mavros). Returns ``(launch_t_ns, recovery_t_ns)`` for the longest
    in-water run, or ``(None, None)`` if no usable altitude source is
    present or the run is too short to be plausible.
    """
    for table, col in _candidate_altitude_tables(namespace):
        if not _table_exists(conn, table):
            continue
        try:
            df = pd.read_sql_query(
                f'SELECT t_ns, {col} AS altitude FROM "{table}" ORDER BY t_ns',
                conn,
            )
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            continue
        if df.empty:
            continue
        result = _detect_from_series(df['t_ns'], df['altitude'])
        if result[0] is not None:
            return result
    return None, None
