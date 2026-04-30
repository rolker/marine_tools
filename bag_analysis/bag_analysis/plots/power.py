"""Power plot: battery V/I/W + RC/PWM channels."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from ..parquet_reader import load_meta, load_topic
from ..topics import topic
from ._common import PlotResult, save_figure, to_elapsed_s


PLOT_NAME = 'power'
TITLE = 'Power: battery + PWM channels'


def generate(
    parquet_dir: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render battery V/I + RC/PWM channels in a 2x1 figure."""
    meta = load_meta(parquet_dir)
    t0 = meta['start_ns']

    battery = load_topic(parquet_dir, topic('mavros/battery', namespace))
    rcout = load_topic(parquet_dir, topic('mavros/rc/out', namespace))

    if battery is None and rcout is None:
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=['battery and rc/out both absent'],
        )

    fig, (ax_bat, ax_pwm) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    summary: list[str] = []

    if battery is not None and 'voltage' in battery.columns:
        t = to_elapsed_s(battery['t_ns'], t0)
        ax_bat.plot(t, battery['voltage'], label='V', linewidth=0.7)
        if 'current' in battery.columns:
            ax_i = ax_bat.twinx()
            ax_i.plot(
                t, battery['current'], label='I',
                color='tab:orange', linewidth=0.7,
            )
            ax_i.set_ylabel('current (A)')
            watts = battery['voltage'] * battery['current']
            summary.append(
                f'- battery: V {battery.voltage.min():.2f}–'
                f'{battery.voltage.max():.2f}, '
                f'I peak {battery.current.max():.2f} A, '
                f'P peak {watts.max():.0f} W',
            )
        else:
            summary.append(
                f'- battery: V {battery.voltage.min():.2f}–'
                f'{battery.voltage.max():.2f}',
            )
        ax_bat.set_ylabel('voltage (V)')
        ax_bat.legend(loc='upper left', fontsize=8)
        ax_bat.grid(alpha=0.3)
    else:
        ax_bat.text(0.5, 0.5, 'battery: n/a',
                    transform=ax_bat.transAxes,
                    ha='center', va='center', alpha=0.5)

    if rcout is not None:
        ch_cols = sorted(
            [c for c in rcout.columns if c.startswith('ch_')],
            key=lambda c: int(c.split('_')[1]),
        )
        t = to_elapsed_s(rcout['t_ns'], t0)
        for c in ch_cols[:8]:  # first 8 channels keep the legend readable
            ax_pwm.plot(t, rcout[c], label=c, linewidth=0.6)
        ax_pwm.set_ylabel('PWM (us)')
        ax_pwm.legend(loc='upper right', fontsize=7, ncol=2)
        ax_pwm.grid(alpha=0.3)
        summary.append(
            f'- rc/out: {len(rcout)} samples, {len(ch_cols)} channels',
        )
    else:
        ax_pwm.text(0.5, 0.5, 'rc/out: n/a',
                    transform=ax_pwm.transAxes,
                    ha='center', va='center', alpha=0.5)

    ax_pwm.set_xlabel('elapsed time (s)')
    fig.suptitle(TITLE)
    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png, summary=summary,
    )
