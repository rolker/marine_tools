"""Track plot: lat/lon path colored by GNSS fix grade."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from ..parquet_reader import load_topic
from ..topics import topic
from ._common import PlotResult, save_figure


PLOT_NAME = 'track'
TITLE = 'Track (colored by GNSS fix grade)'

# sbg_driver SbgGpsPosStatus.type values per the SBG ELLIPSE protocol
FIX_LABELS = {
    0: 'no_solution',
    1: 'unknown',
    2: 'single',
    3: 'sbas',
    4: 'omnistar',
    5: 'rtk_float',
    6: 'rtk_fixed',
    7: 'precise',
}
FIX_COLORS = {
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
    parquet_dir: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render the lat/lon track with fix-grade coloring."""
    df = load_topic(parquet_dir, topic('sensors/sbg/gps_pos', namespace))
    if df is None or df.empty:
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=['sbg/gps_pos absent or empty'],
        )

    fig, ax = plt.subplots(figsize=(8, 8))
    summary: list[str] = []

    if 'status_type' in df.columns:
        for fix_type, group in df.groupby('status_type'):
            label = FIX_LABELS.get(int(fix_type), f'type_{int(fix_type)}')
            color = FIX_COLORS.get(int(fix_type), '#000000')
            ax.scatter(
                group['longitude'], group['latitude'],
                c=color, s=2, label=f'{label} ({len(group)})',
            )
            summary.append(f'- {label}: {len(group)} fixes')
        ax.legend(loc='best', fontsize=8)
    else:
        ax.scatter(df['longitude'], df['latitude'], s=2)
        summary.append(f'- {len(df)} GPS fixes (no status_type column)')

    ax.set_xlabel('longitude')
    ax.set_ylabel('latitude')
    ax.set_title(TITLE)
    ax.set_aspect('equal', adjustable='datalim')
    ax.grid(alpha=0.3)
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png, summary=summary,
    )
