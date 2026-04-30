"""Speed and heading-vs-COG plot."""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib.pyplot as plt

from ..parquet_reader import load_meta, load_topic
from ..topics import topic
from ._common import PlotResult, save_figure, to_elapsed_s


PLOT_NAME = 'speed_heading'
TITLE = 'Speed + heading vs course over ground'


def generate(
    parquet_dir: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render speed (top) and heading-vs-COG (bottom)."""
    meta = load_meta(parquet_dir)
    t0 = meta['start_ns']

    odom = load_topic(parquet_dir, topic('odom', namespace))
    gps_vel = load_topic(parquet_dir, topic('sensors/sbg/gps_vel', namespace))
    gps_hdt = load_topic(parquet_dir, topic('sensors/sbg/gps_hdt', namespace))

    if odom is None and gps_vel is None and gps_hdt is None:
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=['no odom / gps_vel / gps_hdt in this bag'],
        )

    fig, (ax_v, ax_h) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    summary: list[str] = []

    if odom is not None and 'vel_x' in odom.columns:
        speed = (odom['vel_x'] ** 2 + odom['vel_y'] ** 2) ** 0.5
        ax_v.plot(
            to_elapsed_s(odom['t_ns'], t0), speed,
            label='|odom|', linewidth=0.7,
        )
        summary.append(
            f'- odom: max |v| = {speed.max():.2f} m/s, '
            f'mean = {speed.mean():.2f}',
        )
    if gps_vel is not None and {'velocity_n', 'velocity_e'}.issubset(
            gps_vel.columns):
        sog = (
            gps_vel['velocity_n'] ** 2 + gps_vel['velocity_e'] ** 2
        ) ** 0.5
        ax_v.plot(
            to_elapsed_s(gps_vel['t_ns'], t0), sog,
            label='|gps_vel|', linewidth=0.7,
        )
        summary.append(f'- gps_vel: max SOG = {sog.max():.2f} m/s')

    ax_v.set_ylabel('speed (m/s)')
    ax_v.legend(loc='upper right', fontsize=8)
    ax_v.grid(alpha=0.3)

    if gps_vel is not None and 'course' in gps_vel.columns:
        course_deg = gps_vel['course'].apply(
            lambda r: math.degrees(r) % 360,
        )
        ax_h.plot(
            to_elapsed_s(gps_vel['t_ns'], t0), course_deg,
            label='COG', linewidth=0.7, alpha=0.7,
        )
    if gps_hdt is not None and 'true_heading' in gps_hdt.columns:
        # SBG emits true_heading already in degrees per the GpsHdt definition.
        ax_h.plot(
            to_elapsed_s(gps_hdt['t_ns'], t0), gps_hdt['true_heading'],
            label='true heading', linewidth=0.7,
        )
        summary.append(f'- gps_hdt: {len(gps_hdt)} heading messages')

    ax_h.set_ylabel('heading (deg)')
    ax_h.set_xlabel('elapsed time (s)')
    ax_h.set_ylim(0, 360)
    ax_h.legend(loc='upper right', fontsize=8)
    ax_h.grid(alpha=0.3)

    fig.suptitle(TITLE)
    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png, summary=summary,
    )
