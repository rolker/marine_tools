"""Altitude / heave plot: launch + recovery, tide proxy."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from ..sqlite_reader import load_meta, load_topic
from ..topics import topic
from ._common import PlotResult, save_figure, to_elapsed_s


PLOT_NAME = 'altitude_heave'
TITLE = 'Altitude / heave (launch + recovery, tide proxy)'


def generate(
    db_path: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Plot SBG-fused altitude over time."""
    meta = load_meta(db_path)
    t0 = meta['start_ns']

    ekf = load_topic(db_path, topic('sensors/sbg/ekf_nav', namespace))
    if ekf is None or 'altitude' not in ekf.columns:
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=['sensors/sbg/ekf_nav absent or missing altitude'],
        )

    fig, ax = plt.subplots(figsize=(12, 4))
    t = to_elapsed_s(ekf['t_ns'], t0)
    ax.plot(t, ekf['altitude'], linewidth=0.6)
    ax.set_xlabel('elapsed time (s)')
    ax.set_ylabel('altitude (m)')
    ax.set_title(TITLE)
    ax.grid(alpha=0.3)

    summary = [
        f'- altitude range: {ekf.altitude.min():.2f} to '
        f'{ekf.altitude.max():.2f} m',
        f'- altitude std: {ekf.altitude.std():.3f} m '
        '(rough heave proxy in water)',
    ]

    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png, summary=summary,
    )
