"""Speed and heading-vs-COG plot.

Primary sources are mavros: ``odom`` for body-frame speed,
``mavros/global_position/raw/gps_vel`` for ground-truth speed-over-
ground and course-over-ground, and the ``odom`` quaternion for
heading. SBG ``sensors/sbg/gps_vel`` and ``sensors/sbg/gps_hdt`` are
preserved as supplementary traces — useful for cross-checks while the
SBG Ellipse-D is on loan, harmless when those topics are absent.

Headings and CoG are reported in compass-true degrees (0° = north,
clockwise), independent of the underlying frame convention (ENU yaw
in odom, NED course in SBG, ENU east/north in TwistStamped).
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib.pyplot as plt

from ..sqlite_reader import load_meta, load_topic
from ..topics import topic
from ._common import PlotResult, save_figure, to_elapsed_s


PLOT_NAME = 'speed_heading'
TITLE = 'Speed + heading vs course over ground'


def generate(
    db_path: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render speed (top) and heading-vs-COG (bottom)."""
    meta = load_meta(db_path)
    t0 = meta['start_ns']

    odom = load_topic(db_path, topic('odom', namespace))
    mavros_vel = load_topic(
        db_path, topic('mavros/global_position/raw/gps_vel', namespace),
    )
    sbg_vel = load_topic(db_path, topic('sensors/sbg/gps_vel', namespace))
    sbg_hdt = load_topic(db_path, topic('sensors/sbg/gps_hdt', namespace))

    if all(df is None for df in (odom, mavros_vel, sbg_vel, sbg_hdt)):
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=[
                'no odom / mavros gps_vel / sbg gps_vel / sbg gps_hdt in bag',
            ],
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

    if mavros_vel is not None and {'vel_x', 'vel_y'}.issubset(
            mavros_vel.columns):
        # mavros/global_position/raw/gps_vel is ENU TwistStamped:
        # vel_x = east, vel_y = north, vel_z = up.
        sog = (mavros_vel['vel_x'] ** 2 + mavros_vel['vel_y'] ** 2) ** 0.5
        ax_v.plot(
            to_elapsed_s(mavros_vel['t_ns'], t0), sog,
            label='|mavros gps_vel|', linewidth=0.7,
        )
        cog_deg = mavros_vel.apply(
            lambda r: _enu_velocity_to_compass(r['vel_x'], r['vel_y']),
            axis=1,
        )
        ax_h.plot(
            to_elapsed_s(mavros_vel['t_ns'], t0), cog_deg,
            label='COG (mavros)', linewidth=0.7, alpha=0.8,
        )
        summary.append(f'- mavros gps_vel: max SOG = {sog.max():.2f} m/s')

    if sbg_vel is not None and {'velocity_n', 'velocity_e'}.issubset(
            sbg_vel.columns):
        sog = (
            sbg_vel['velocity_n'] ** 2 + sbg_vel['velocity_e'] ** 2
        ) ** 0.5
        ax_v.plot(
            to_elapsed_s(sbg_vel['t_ns'], t0), sog,
            label='|sbg gps_vel|', linewidth=0.6, alpha=0.6,
            linestyle='--',
        )
        if 'course' in sbg_vel.columns:
            sbg_cog = sbg_vel['course'].apply(
                lambda r: math.degrees(r) % 360,
            )
            ax_h.plot(
                to_elapsed_s(sbg_vel['t_ns'], t0), sbg_cog,
                label='COG (sbg)', linewidth=0.6, alpha=0.5,
                linestyle='--',
            )

    if odom is not None and {'q_w', 'q_x', 'q_y', 'q_z'}.issubset(
            odom.columns):
        heading = odom.apply(
            lambda r: _enu_quaternion_to_compass(
                r['q_w'], r['q_x'], r['q_y'], r['q_z'],
            ),
            axis=1,
        )
        ax_h.plot(
            to_elapsed_s(odom['t_ns'], t0), heading,
            label='heading (odom)', linewidth=0.8,
        )

    if sbg_hdt is not None and 'true_heading' in sbg_hdt.columns:
        # SbgGpsHdt true_heading is already in degrees (compass).
        ax_h.plot(
            to_elapsed_s(sbg_hdt['t_ns'], t0), sbg_hdt['true_heading'],
            label='heading (sbg)', linewidth=0.6, alpha=0.5,
            linestyle='--',
        )
        summary.append(f'- sbg gps_hdt: {len(sbg_hdt)} heading messages')

    ax_v.set_ylabel('speed (m/s)')
    ax_v.legend(loc='upper right', fontsize=8)
    ax_v.grid(alpha=0.3)

    ax_h.set_ylabel('heading / COG (deg, compass)')
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


def _enu_velocity_to_compass(vel_east: float, vel_north: float) -> float:
    """Convert an ENU velocity vector to compass-true degrees (0=N, CW)."""
    if not (math.isfinite(vel_east) and math.isfinite(vel_north)):
        return float('nan')
    if vel_east == 0.0 and vel_north == 0.0:
        return float('nan')
    return math.degrees(math.atan2(vel_east, vel_north)) % 360


def _enu_quaternion_to_compass(
    w: float, x: float, y: float, z: float,
) -> float:
    """ENU body-orientation quaternion → compass-true heading degrees.

    Yaw is rotation about the up axis; in ENU yaw=0 means the body x
    axis points east. Compass heading = (90 - yaw_deg) mod 360 so that
    bow-north reads 0°, bow-east reads 90°.
    """
    if not all(math.isfinite(c) for c in (w, x, y, z)):
        return float('nan')
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return (90.0 - math.degrees(yaw)) % 360
