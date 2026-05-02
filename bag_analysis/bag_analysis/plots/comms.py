"""
Comms plot: udp_bridge throughput + drops over time.

Reads the per-message rate columns the udp_bridge extractors now emit
directly (no more `.diff()` of cumulative counters, which gave
bytes-per-window with no time-delta meaning). Y-axis is Mbps so future
overlays with Starlink dish throughput (which reports bits/sec
already) and MikroTik tx-byte counters (bytes cumulative) line up
under the same convention — see `_common.py` for the unit table.

Top panel: BridgeInfo wire-out (sum of message+overhead+resend
success+failed) and BridgeInfo received. Bottom panel: TopicStatistics
send drops, broken into failed and rate-limited components.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from ._common import (
    bytes_per_s_to_mbps,
    PlotResult,
    save_figure,
    to_elapsed_s,
)
from ..sqlite_reader import load_meta, load_topic
from ..topics import topic


PLOT_NAME = 'comms'
TITLE = 'Comms: udp_bridge throughput + drops (Mbps)'


def generate(
    db_path: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render bandwidth (top) and drops (bottom) for udp_bridge."""
    meta = load_meta(db_path)
    t0 = meta['start_ns']

    bridge = load_topic(db_path, topic('udp_bridge/bridge_info', namespace))
    stats = load_topic(
        db_path, topic('udp_bridge/topic_statistics', namespace),
    )

    if (bridge is None or bridge.empty) and (stats is None or stats.empty):
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=[
                'no udp_bridge/bridge_info or udp_bridge/topic_statistics '
                'in bag',
            ],
        )

    fig, (ax_b, ax_d) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    summary: list[str] = []
    bridge_traces = 0
    drop_traces = 0

    if bridge is not None and not bridge.empty:
        t = to_elapsed_s(bridge['t_ns'], t0)
        if 'wire_out_bytes_per_second' in bridge.columns:
            wire_out_mbps = bytes_per_s_to_mbps(
                bridge['wire_out_bytes_per_second'],
            )
            ax_b.plot(t, wire_out_mbps, label='wire out (BOAT→OP)',
                      linewidth=0.7)
            bridge_traces += 1
            summary.append(
                f'- wire-out peak: {wire_out_mbps.max():.2f} Mbps, '
                f'mean {wire_out_mbps.mean():.2f}',
            )
        if 'received_bytes_per_second' in bridge.columns:
            recv_mbps = bytes_per_s_to_mbps(
                bridge['received_bytes_per_second'],
            )
            ax_b.plot(t, recv_mbps, label='received (BOAT←OP)',
                      linewidth=0.7)
            bridge_traces += 1
            summary.append(
                f'- received peak: {recv_mbps.max():.2f} Mbps, '
                f'mean {recv_mbps.mean():.2f}',
            )

    ax_b.set_ylabel('Mbps')
    ax_b.grid(alpha=0.3)
    if bridge_traces:
        ax_b.legend(loc='upper right', fontsize=8)

    if stats is not None and not stats.empty:
        t = to_elapsed_s(stats['t_ns'], t0)
        if 'send_failed_bytes_per_second' in stats.columns:
            failed_mbps = bytes_per_s_to_mbps(
                stats['send_failed_bytes_per_second'],
            )
            ax_d.plot(
                t, failed_mbps,
                label='send failed', color='tab:red', linewidth=0.7,
            )
            drop_traces += 1
            summary.append(
                f'- send failed peak: {failed_mbps.max():.3f} Mbps',
            )
        if 'send_dropped_bytes_per_second' in stats.columns:
            dropped_mbps = bytes_per_s_to_mbps(
                stats['send_dropped_bytes_per_second'],
            )
            ax_d.plot(
                t, dropped_mbps,
                label='rate-limit dropped', color='tab:orange',
                linewidth=0.7,
            )
            drop_traces += 1
            summary.append(
                f'- rate-limit dropped peak: {dropped_mbps.max():.3f} Mbps',
            )

    ax_d.set_ylabel('Mbps')
    ax_d.set_xlabel('elapsed time (s)')
    ax_d.grid(alpha=0.3)
    if drop_traces:
        ax_d.legend(loc='upper right', fontsize=8)

    fig.suptitle(TITLE)
    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png, summary=summary,
    )
