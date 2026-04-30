"""Sensor health plot: diagnostics over time + per-topic message rates.

Top panel: stacked counts of WARN/ERROR statuses from /diagnostics.
Bottom panel: 15 highest-volume topics by average rate (Hz). Topics
with zero messages stand out as missing bars where you'd expect them.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from ..parquet_reader import load_index, load_meta, load_topic
from ._common import PlotResult, save_figure, to_elapsed_s


PLOT_NAME = 'sensor_health'
TITLE = 'Sensor health: diagnostics + topic rates'


def generate(
    parquet_dir: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render diagnostics-over-time and per-topic rate bars."""
    meta = load_meta(parquet_dir)
    t0 = meta['start_ns']
    duration_s = meta['duration_ns'] / 1e9

    diag = load_topic(parquet_dir, '/diagnostics')
    index = load_index(parquet_dir)

    fig, (ax_d, ax_r) = plt.subplots(2, 1, figsize=(12, 7))
    summary: list[str] = []
    warnings: list[str] = []

    if diag is not None and 'n_warn' in diag.columns:
        t = to_elapsed_s(diag['t_ns'], t0)
        ax_d.fill_between(
            t, diag['n_error'], step='post',
            color='tab:red', alpha=0.6, label='ERROR',
        )
        ax_d.fill_between(
            t, diag['n_warn'], step='post',
            color='tab:orange', alpha=0.5, label='WARN',
        )
        ax_d.plot(
            t, diag['n_status'],
            color='tab:gray', linewidth=0.5, label='total',
        )
        ax_d.set_ylabel('# statuses')
        ax_d.set_xlabel('elapsed time (s)')
        ax_d.legend(loc='upper right', fontsize=8)
        ax_d.set_title('/diagnostics levels over time')
        ax_d.grid(alpha=0.3)
        peak_err = int(diag['n_error'].max())
        peak_warn = int(diag['n_warn'].max())
        summary.append(
            f'- /diagnostics: {len(diag)} msgs, '
            f'peak ERROR {peak_err}, peak WARN {peak_warn}',
        )
    else:
        ax_d.text(0.5, 0.5, '/diagnostics: n/a',
                  transform=ax_d.transAxes,
                  ha='center', va='center', alpha=0.5)
        warnings.append('/diagnostics absent or empty')

    if duration_s > 0 and index:
        sorted_topics = sorted(
            index.items(), key=lambda kv: kv[1]['count'],
        )
        names = [name for name, _ in sorted_topics[-15:]]
        rates = [
            entry['count'] / duration_s for _, entry in sorted_topics[-15:]
        ]
        ax_r.barh(range(len(names)), rates, color='tab:blue')
        ax_r.set_yticks(range(len(names)))
        ax_r.set_yticklabels(
            [n if len(n) <= 50 else '…' + n[-47:] for n in names],
            fontsize=7,
        )
        ax_r.set_xlabel('avg messages / second')
        ax_r.set_title('top 15 topics by message count')
        ax_r.grid(alpha=0.3, axis='x')

    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png,
        summary=summary, warnings=warnings,
    )
