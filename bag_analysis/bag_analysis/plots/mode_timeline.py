"""Mode timeline plot.

Three-lane step plot of:
  - mavros/state.mode                       (Pixhawk flight mode)
  - marine/status/mission_manager           (autonomy heartbeat)
  - behavior_tree_log.last_current_status   (Nav2 BT state)

Goal: answer "what was the boat doing minute-by-minute" at a glance.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from ..parquet_reader import load_meta, load_topic
from ..topics import topic
from ._common import PlotResult, save_figure, step_plot_strings, to_elapsed_s


PLOT_NAME = 'mode_timeline'
TITLE = 'Mode timeline'


def generate(
    parquet_dir: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render the three-lane mode timeline."""
    meta = load_meta(parquet_dir)
    t0 = meta['start_ns']

    state = load_topic(parquet_dir, topic('mavros/state', namespace))
    mission = load_topic(
        parquet_dir, topic('marine/status/mission_manager', namespace),
    )
    bt = load_topic(parquet_dir, topic('behavior_tree_log', namespace))

    if state is None and mission is None and bt is None:
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=['none of mavros/state, mission_manager, '
                      'behavior_tree_log are in this bag'],
        )

    fig, axes = plt.subplots(3, 1, figsize=(12, 6), sharex=True)
    summary: list[str] = []
    warnings: list[str] = []

    # Lane 1: mavros mode
    if state is not None and 'mode' in state.columns:
        step_plot_strings(
            axes[0],
            to_elapsed_s(state['t_ns'], t0),
            state['mode'],
            'mavros mode',
        )
        summary.append(
            f'- mavros: {len(state)} state msgs, '
            f'{state["mode"].nunique()} unique modes',
        )
    else:
        axes[0].text(0.5, 0.5, 'mavros/state: n/a',
                     transform=axes[0].transAxes,
                     ha='center', va='center', alpha=0.5)
        warnings.append('mavros/state absent')

    # Lane 2: mission_manager — schema varies, fall back to heartbeat ticks
    if mission is not None and 'status' in mission.columns:
        step_plot_strings(
            axes[1],
            to_elapsed_s(mission['t_ns'], t0),
            mission['status'].astype(str),
            'mission status',
        )
        summary.append(f'- mission_manager: {len(mission)} msgs')
    elif mission is not None:
        elapsed = to_elapsed_s(mission['t_ns'], t0)
        axes[1].plot(elapsed, [1] * len(elapsed), '|', markersize=4)
        axes[1].set_ylabel('mission hb')
        axes[1].set_yticks([])
        summary.append(f'- mission_manager: {len(mission)} heartbeats')
    else:
        axes[1].text(0.5, 0.5, 'mission_manager: n/a',
                     transform=axes[1].transAxes,
                     ha='center', va='center', alpha=0.5)
        warnings.append('mission_manager absent')

    # Lane 3: BT current status
    if bt is not None and 'last_current_status' in bt.columns:
        step_plot_strings(
            axes[2],
            to_elapsed_s(bt['t_ns'], t0),
            bt['last_current_status'].astype(str),
            'BT status',
        )
        summary.append(f'- behavior_tree_log: {len(bt)} msgs')
    else:
        axes[2].text(0.5, 0.5, 'behavior_tree_log: n/a',
                     transform=axes[2].transAxes,
                     ha='center', va='center', alpha=0.5)
        warnings.append('behavior_tree_log absent')

    axes[2].set_xlabel('elapsed time (s)')
    fig.suptitle(TITLE)
    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png,
        summary=summary, warnings=warnings,
    )
