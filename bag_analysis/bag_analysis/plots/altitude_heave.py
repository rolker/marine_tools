"""Altitude / heave plot: launch + recovery, tide proxy.

Primary source is mavros NavSatFix (``mavros/global_position/raw/fix``),
which publishes MSL altitude on every ArduPilot/mavros boat. SBG
``sensors/sbg/ekf_nav`` is a fallback for SBG-only bags (note: ekf_nav
altitude is ellipsoidal, not MSL — different absolute value, same
crane-in/out shape).

Raw GPS altitude has occasional sub-second glitches (53 m → 28 m → 4 m
spikes were observed in the 2026-04-29 cod rock bag) that warp the
y-axis and corrupt the min/max stats. A 10 s rolling-median smoothing
rejects them cleanly without smearing the launch/recovery transitions.
"""

from __future__ import annotations

from pathlib import Path

import math

import matplotlib.pyplot as plt
import pandas as pd

from ..sqlite_reader import load_meta, load_topic
from ..topics import topic
from ._common import PlotResult, save_figure, to_elapsed_s


PLOT_NAME = 'altitude_heave'
TITLE = 'Altitude / heave (launch + recovery, tide proxy)'

# Rolling-median window for GPS altitude glitch rejection. 10 s is wide
# enough to swallow multi-second GPS glitches but narrow enough to keep
# the launch/recovery transition shape intact.
_DEFAULT_SMOOTH_WINDOW_S = 10.0


def generate(
    db_path: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Plot altitude over time, smoothed with a rolling-median window."""
    meta = load_meta(db_path)
    t0 = meta['start_ns']

    nav = load_topic(
        db_path, topic('mavros/global_position/raw/fix', namespace),
    )
    if nav is not None and 'altitude' in nav.columns:
        return _render(
            output_dir, nav, t0,
            source='mavros/global_position/raw/fix (MSL)',
        )

    ekf = load_topic(db_path, topic('sensors/sbg/ekf_nav', namespace))
    if ekf is not None and 'altitude' in ekf.columns:
        return _render(
            output_dir, ekf, t0,
            source='sensors/sbg/ekf_nav (ellipsoidal, fallback)',
        )

    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE,
        warnings=[
            'no altitude in mavros/global_position/raw/fix or '
            'sensors/sbg/ekf_nav',
        ],
    )


def _render(output_dir: Path, df: pd.DataFrame, t0: int, *, source: str,
            window_s: float = _DEFAULT_SMOOTH_WINDOW_S) -> PlotResult:
    t = to_elapsed_s(df['t_ns'], t0)
    raw = df['altitude']
    smoothed, window_n = _rolling_median_by_seconds(df['t_ns'], raw, window_s)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(t, raw, linewidth=0.4, alpha=0.4, label='raw')
    ax.plot(t, smoothed, linewidth=1.0, label=f'{window_s:.0f}s median')
    ax.set_xlabel('elapsed time (s)')
    ax.set_ylabel('altitude (m)')
    ax.set_title(TITLE)
    ax.legend(loc='upper right', fontsize=8)
    ax.grid(alpha=0.3)

    summary = [
        f'- source: `{source}`',
        f'- altitude range (smoothed, {window_s:.0f}s median): '
        f'{smoothed.min():.2f} to {smoothed.max():.2f} m',
        f'- altitude std (smoothed): {smoothed.std():.3f} m '
        '(rough heave proxy in water)',
        f'- smoothing window: {window_n} samples',
    ]

    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png, summary=summary,
    )


def _rolling_median_by_seconds(
    t_ns: pd.Series, values: pd.Series, window_s: float,
) -> tuple[pd.Series, int]:
    """Apply a centered rolling median sized in seconds.

    Estimates the sample period from the median of ``diff(t_ns)`` and
    converts to a sample count. Falls back to no smoothing when the
    series is too short to estimate or the window covers fewer than
    three samples.
    """
    if len(t_ns) < 3:
        return values, 1
    dt_ns = float(t_ns.diff().median())
    if not math.isfinite(dt_ns) or dt_ns <= 0:
        return values, 1
    window_n = max(int(round(window_s * 1e9 / dt_ns)), 3)
    return (
        values.rolling(window=window_n, center=True, min_periods=1).median(),
        window_n,
    )
