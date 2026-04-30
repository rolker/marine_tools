"""Comms plot: UDP bridge throughput + drops over time."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from ..parquet_reader import load_meta, load_topic
from ..topics import topic
from ._common import PlotResult, save_figure, to_elapsed_s


PLOT_NAME = 'comms'
TITLE = 'Comms: UDP bridge throughput + drops'


def generate(
    parquet_dir: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render byte-rate (top) and drop-rate (bottom) for udp_bridge."""
    meta = load_meta(parquet_dir)
    t0 = meta['start_ns']

    stats = load_topic(
        parquet_dir, topic('udp_bridge/topic_statistics', namespace),
    )
    if stats is None or stats.empty:
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=['udp_bridge/topic_statistics absent'],
        )

    fig, (ax_b, ax_d) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    summary: list[str] = []

    t = to_elapsed_s(stats['t_ns'], t0)
    if 'total_bytes_received' in stats.columns:
        ax_b.plot(
            t, stats['total_bytes_received'].diff().fillna(0),
            label='B/sample in', linewidth=0.7,
        )
    if 'total_bytes_sent' in stats.columns:
        ax_b.plot(
            t, stats['total_bytes_sent'].diff().fillna(0),
            label='B/sample out', linewidth=0.7,
        )
    ax_b.set_ylabel('bytes / sample')
    ax_b.legend(loc='upper right', fontsize=8)
    ax_b.grid(alpha=0.3)

    if 'total_drops' in stats.columns:
        ax_d.plot(
            t, stats['total_drops'].diff().fillna(0),
            color='tab:red', linewidth=0.7,
        )
        ax_d.set_ylabel('drops / sample')
        ax_d.grid(alpha=0.3)
        final_drops = (
            int(stats['total_drops'].iloc[-1]) if len(stats) else 0
        )
        summary.append(
            f'- udp_bridge: {len(stats)} stats msgs, '
            f'final cumulative drops {final_drops}',
        )

    ax_d.set_xlabel('elapsed time (s)')
    fig.suptitle(TITLE)
    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png, summary=summary,
    )
