"""
Mode timeline plot.

Three-lane step plot of:
  - mavros/state.mode                                 (FCU flight mode)
  - marine/heartbeat.piloting_mode                    (autonomy-side mode)
  - marine/status/mission_manager.current_nav_task    (active mission task)

Goal: answer "what was the boat doing minute-by-minute" at a glance,
across the three layers operators care about (FCU, autonomy stack,
mission). The previous behavior_tree_log lane was too noisy to be
useful — the active task name is the durable signal.

When the launch/recovery window is recorded in `_bag_meta`, the x-axis
is trimmed to the in-water window so crane time doesn't compress the
interesting parts.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from ._common import PlotResult, save_figure, step_plot_strings, to_elapsed_s
from ..sqlite_reader import load_meta, load_topic
from ..topics import topic


PLOT_NAME = 'mode_timeline'
TITLE = 'Mode timeline'


def _trim_to_window(
    df: pd.DataFrame | None,
    launch_t_ns: int | None,
    recovery_t_ns: int | None,
) -> pd.DataFrame | None:
    if df is None or launch_t_ns is None or recovery_t_ns is None:
        return df
    return df[
        (df['t_ns'] >= launch_t_ns) & (df['t_ns'] <= recovery_t_ns)
    ].reset_index(drop=True)


def _nonblank(df: pd.DataFrame | None, col: str) -> pd.DataFrame | None:
    """Filter rows where the value column is non-null and non-empty."""
    if df is None or col not in df.columns:
        return None
    s = df[col].astype(str)
    keep = s.notna() & (s != '') & (s != 'None') & (s.str.strip() != '')
    valid = df[keep].reset_index(drop=True)
    return valid if len(valid) else None


def generate(
    db_path: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render the three-lane mode timeline."""
    meta = load_meta(db_path)
    t0 = meta['start_ns']
    launch_t_ns = meta.get('launch_t_ns')
    recovery_t_ns = meta.get('recovery_t_ns')

    state = load_topic(db_path, topic('mavros/state', namespace))
    heartbeat = load_topic(db_path, topic('marine/heartbeat', namespace))
    mission = load_topic(
        db_path, topic('marine/status/mission_manager', namespace),
    )

    state = _trim_to_window(state, launch_t_ns, recovery_t_ns)
    heartbeat = _trim_to_window(heartbeat, launch_t_ns, recovery_t_ns)
    mission = _trim_to_window(mission, launch_t_ns, recovery_t_ns)

    if state is None and heartbeat is None and mission is None:
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=['none of mavros/state, marine/heartbeat, '
                      'mission_manager are in this bag'],
        )

    fig, axes = plt.subplots(3, 1, figsize=(12, 6), sharex=True)
    summary: list[str] = []
    warnings: list[str] = []

    # Lane 1: FCU mode
    if state is not None and 'mode' in state.columns:
        step_plot_strings(
            axes[0],
            to_elapsed_s(state['t_ns'], t0),
            state['mode'].astype(str),
            'FCU mode',
        )
        summary.append(
            f'- FCU mode: {len(state)} state msgs, '
            f'{state["mode"].nunique()} unique modes',
        )
    else:
        axes[0].text(0.5, 0.5, 'mavros/state: n/a',
                     transform=axes[0].transAxes,
                     ha='center', va='center', alpha=0.5)
        warnings.append('mavros/state absent')

    # Lane 2: piloting state (autonomy-side)
    piloting = _nonblank(heartbeat, 'piloting_mode')
    if piloting is not None:
        step_plot_strings(
            axes[1],
            to_elapsed_s(piloting['t_ns'], t0),
            piloting['piloting_mode'].astype(str),
            'piloting',
        )
        summary.append(
            f'- piloting state: '
            f'{len(piloting)}/{len(heartbeat) if heartbeat is not None else 0} populated, '
            f'{piloting["piloting_mode"].nunique()} unique',
        )
    else:
        axes[1].text(0.5, 0.5, 'marine/heartbeat.piloting_mode: n/a',
                     transform=axes[1].transAxes,
                     ha='center', va='center', alpha=0.5)
        warnings.append('marine/heartbeat.piloting_mode absent or empty')

    # Lane 3: current task (mission manager)
    task = _nonblank(mission, 'current_nav_task')
    if task is not None:
        step_plot_strings(
            axes[2],
            to_elapsed_s(task['t_ns'], t0),
            task['current_nav_task'].astype(str),
            'current task',
        )
        summary.append(
            f'- current task: '
            f'{len(task)}/{len(mission) if mission is not None else 0} populated, '
            f'{task["current_nav_task"].nunique()} unique',
        )
    else:
        axes[2].text(
            0.5, 0.5, 'mission_manager.current_nav_task: n/a',
            transform=axes[2].transAxes,
            ha='center', va='center', alpha=0.5,
        )
        warnings.append('mission_manager.current_nav_task absent or empty')

    axes[2].set_xlabel('elapsed time (s)')
    if launch_t_ns is not None and recovery_t_ns is not None:
        fig.suptitle(f'{TITLE} (in-water window)')
    else:
        fig.suptitle(TITLE)
    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png,
        summary=summary, warnings=warnings,
    )
