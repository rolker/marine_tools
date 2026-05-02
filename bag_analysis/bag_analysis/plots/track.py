"""
Track plot: lat/lon path colored by GNSS status.

Primary source is mavros NavSatFix (``mavros/global_position/raw/fix``);
SBG ``sensors/sbg/gps_pos`` is a fallback for SBG-only bags. Coloring
uses NavSatFix's ``status`` field — coarser than SBG's RTK fix grade
but available on every ArduPilot/mavros boat without an external INS.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from ._common import PlotResult, save_figure
from ..sqlite_reader import load_topic
from ..topics import topic


PLOT_NAME = 'track'
TITLE = 'Track (colored by GNSS status)'

# sensor_msgs/NavSatStatus.status values (mavros primary path)
NAVSAT_LABELS = {
    -1: 'no_fix',
    0: 'fix',
    1: 'sbas',
    2: 'gbas',
}
NAVSAT_COLORS = {
    -1: '#888888',
    0: '#ff7f0e',
    1: '#1f77b4',
    2: '#0066cc',
}

# sbg_driver SbgGpsPosStatus.type values (SBG fallback path)
SBG_FIX_LABELS = {
    0: 'no_solution',
    1: 'unknown',
    2: 'single',
    3: 'sbas',
    4: 'omnistar',
    5: 'rtk_float',
    6: 'rtk_fixed',
    7: 'precise',
}
SBG_FIX_COLORS = {
    0: '#888888',
    1: '#bbbbbb',
    2: '#ff7f0e',
    3: '#1f77b4',
    4: '#9467bd',
    5: '#2ca02c',
    6: '#0066cc',
    7: '#0033aa',
}


def generate(
    db_path: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render the lat/lon track with status coloring."""
    mavros_fix = load_topic(
        db_path, topic('mavros/global_position/raw/fix', namespace),
    )
    if mavros_fix is not None and not mavros_fix.empty:
        return _render(
            output_dir, mavros_fix,
            status_col='status',
            labels=NAVSAT_LABELS, colors=NAVSAT_COLORS,
            source='mavros/global_position/raw/fix',
        )

    sbg_pos = load_topic(db_path, topic('sensors/sbg/gps_pos', namespace))
    if sbg_pos is not None and not sbg_pos.empty:
        return _render(
            output_dir, sbg_pos,
            status_col='status_type',
            labels=SBG_FIX_LABELS, colors=SBG_FIX_COLORS,
            source='sensors/sbg/gps_pos (fallback)',
        )

    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE,
        warnings=[
            'no mavros/global_position/raw/fix or sensors/sbg/gps_pos in bag',
        ],
    )


def _render(
    output_dir: Path,
    df,
    *,
    status_col: str,
    labels: dict[int, str],
    colors: dict[int, str],
    source: str,
) -> PlotResult:
    """Scatter lat/lon with categorical coloring by ``status_col``."""
    fig, ax = plt.subplots(figsize=(8, 8))
    summary: list[str] = [f'- source: `{source}`, {len(df)} fixes']

    if status_col in df.columns:
        for status_val, group in df.groupby(status_col):
            label = labels.get(int(status_val), f'status_{int(status_val)}')
            color = colors.get(int(status_val), '#000000')
            ax.scatter(
                group['longitude'], group['latitude'],
                c=color, s=2, label=f'{label} ({len(group)})',
            )
            summary.append(f'  - {label}: {len(group)} fixes')
        ax.legend(loc='best', fontsize=8)
    else:
        ax.scatter(df['longitude'], df['latitude'], s=2)
        summary.append(f'  - no `{status_col}` column; uncolored scatter')

    ax.set_xlabel('longitude')
    ax.set_ylabel('latitude')
    ax.set_title(TITLE)
    ax.set_aspect('equal', adjustable='datalim')
    ax.grid(alpha=0.3)
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png, summary=summary,
    )
