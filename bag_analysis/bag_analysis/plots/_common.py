"""Shared helpers for plot modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import pandas as pd


@dataclass
class PlotResult:
    """Result of a plot generator.

    `png_path` is None when the plot couldn't render (e.g. all source
    topics absent). Callers render placeholders in the summary.md from
    `warnings` in that case.
    """

    plot_name: str
    title: str
    png_path: Optional[Path] = None
    summary: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def to_elapsed_s(t_ns: pd.Series, t0_ns: int) -> pd.Series:
    """Convert nanosecond timestamps to elapsed seconds from t0."""
    return (t_ns - t0_ns) / 1e9


# Unit convention for bandwidth columns across plots:
#   * udp_bridge_interfaces `*_bytes_per_second` and `DataRates.*_bytes_per_second`
#                           → bytes/sec (multiply by 8 then ÷ 1e6 → Mbps)
#   * MikroTik `tx-byte` / `rx-byte` cumulative counters
#                           → bytes  (diff over interval, then same conversion)
#   * Starlink `*_throughput_bps`
#                           → bits/sec (just ÷ 1e6 → Mbps; do NOT multiply by 8)
#
# Pick Mbps everywhere when rendering; convert at extractor or plot
# time. The 8× factor between bytes/sec and bits/sec has been observed
# to cause attribution errors in network-analysis writeups when these
# sources are combined without an explicit convention — keep it
# centralised here.

def bytes_per_s_to_mbps(bps: pd.Series) -> pd.Series:
    """Convert bytes/sec to megabits/sec."""
    return bps * 8.0 / 1e6


def save_figure(fig, output_dir: Path, plot_name: str) -> Path:
    """Save a figure to <output_dir>/<plot_name>.png and close it."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f'{plot_name}.png'
    fig.savefig(path, dpi=120, bbox_inches='tight')
    plt.close(fig)
    return path


def step_plot_strings(ax, t, values, ylabel: str) -> None:
    """Step-plot a string-valued column with categorical y-ticks.

    Maps unique strings to integer y-positions and labels them, since
    matplotlib's step renderer expects numeric y.
    """
    vals = values.astype(str)
    unique = list(dict.fromkeys(vals.tolist()))
    index = vals.map({v: i for i, v in enumerate(unique)})
    ax.step(t, index, where='post', linewidth=0.8)
    ax.set_yticks(range(len(unique)))
    ax.set_yticklabels(unique, fontsize=7)
    ax.set_ylabel(ylabel)
