"""
Power plot: battery voltage + RC/PWM channels.

BizzyBoat has no current sensor; ``BatteryState.current`` reads as a
constant placeholder (~0.01 A) and any current/watts derived from it
is meaningless. The voltage trace IS real and useful for end-of-day
SoC checks; PWM channels are the closest available proxy for load.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from ._common import PlotResult, save_figure, to_elapsed_s
from ..sqlite_reader import load_meta, load_topic
from ..topics import topic


PLOT_NAME = 'power'
TITLE = 'Battery voltage + PWM channels'


def generate(
    db_path: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render battery voltage + RC/PWM channels in a 2x1 figure."""
    meta = load_meta(db_path)
    t0 = meta['start_ns']

    battery = load_topic(db_path, topic('mavros/battery', namespace))
    rcout = load_topic(db_path, topic('mavros/rc/out', namespace))

    if battery is None and rcout is None:
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=['battery and rc/out both absent'],
        )

    fig, (ax_bat, ax_pwm) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    summary: list[str] = []

    if battery is not None and 'voltage' in battery.columns:
        t = to_elapsed_s(battery['t_ns'], t0)
        ax_bat.plot(t, battery['voltage'], linewidth=0.7)
        ax_bat.set_ylabel('voltage (V)')
        ax_bat.grid(alpha=0.3)
        summary.append(
            f'- battery voltage: '
            f'{battery.voltage.min():.2f}–{battery.voltage.max():.2f} V '
            f'(mean {battery.voltage.mean():.2f})',
        )
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
