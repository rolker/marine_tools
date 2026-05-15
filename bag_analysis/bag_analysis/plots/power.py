"""
Power plot — voltage + V-drop-derived current/power for BizzyBoat.

BizzyBoat has no current sensor (`BatteryState.current` reads as a
constant placeholder, ~0.01 A) so naive watts-from-current is
meaningless. This plot uses **voltage drop under load** as the proxy,
calibrated against a single absolute current measurement
(2026-04-27, 67 A at PWM 1938, post-parallel, in-water under load —
external Bluetooth DC clamp meter; details in
`unh_echoboats_project11/docs/logs/2026/2026-04-27_dev_logs.md`),
combined with the multi-point V-drop / PWM curve from PR #90
(`docs/bizzyboat_dynamics_2026-04-24.md`).

Model
-----

    V_load(t) = V_oc(t) - I(t) * R_int

Inverting for current:

    I(t) = (V_oc(t) - V_load(t)) / R_int

Where:

- ``V_oc(t)`` is estimated as the 90 s rolling **maximum** of voltage
  during near-idle PWM windows (matches PR #90's idle-baseline
  method); slowly drifts with discharge but is stable across
  short-term throttle events.
- ``R_int`` is hardcoded at 12.9 mΩ — derived from the calibration
  point: 0.864 V V-drop at 67 A → 0.864 / 67 ≈ 12.9 mΩ for the
  parallel pair (2× Torqeedo Power 24-3500).

Caveats (all surfaced in the summary text):

- **Single absolute calibration point.** Other I estimates rely on
  V-drop curve shape from one day's data. Absolute values ±~20 %.
- **R_int is treated as constant.** Real packs vary ±10–20 % with
  temperature and SoC.
- **Voltage noise floor ~50 mV** → at low PWM (V_drop < 100 mV),
  the I estimate has ~±4 A jitter. Don't trust idle-current numbers
  below that.
- **LiFePO4 voltage-based SoC is NOT reported.** OCV is flat across
  ~30–90 % SoC; voltage tells you nothing about state-of-charge in
  the normal operating range. Energy-integrated estimate is the
  honest figure.

These constants belong per-platform; hardcoded here for BizzyBoat.
When other platforms get analyzed, factor into
`bag_analysis/platforms/<name>.yaml` or similar.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ._common import PlotResult, save_figure, to_elapsed_s
from ..sqlite_reader import load_meta, load_topic
from ..topics import topic


PLOT_NAME = 'power'
TITLE = 'Battery voltage + estimated current/power (V-drop model)'

# BizzyBoat platform calibration constants.
# Derived from 2026-04-27 external clamp meter (67 A @ PWM 1938) +
# PR #90 V-drop curve (0.864 V drop at peak).
_R_INT_OHMS = 0.0129
_CAPACITY_WH = 7000.0  # 2 x 3500 Wh Torqeedo Power 24-3500 LiFePO4
_PEAK_REF_CURRENT_A = 67.0
_PEAK_REF_PWM = 1938

# Idle PWM range used to find quiescent voltage for V_oc estimation.
_IDLE_PWM_LOW = 1480
_IDLE_PWM_HIGH = 1520

# 90 s rolling-max window for V_oc baseline (matches PR #90 method).
_V_OC_WINDOW_S = 90

# Resting-voltage detection: averaging window at start/end of in-water.
_RESTING_AVG_S = 30


def _thruster_channels(rcout: pd.DataFrame) -> list[str]:
    """
    Pick the RCOut columns most likely to be thruster outputs.

    Heuristic: ``ch_*`` columns whose values vary most. Two channels
    are returned (BizzyBoat is two-thruster) when at least two qualify;
    fewer when fewer ``ch_*`` columns have non-trivial variance. The
    plot still works with a single channel — just less robust at
    classifying "idle".
    """
    ch_cols = [c for c in rcout.columns if c.startswith('ch_')]
    if not ch_cols:
        return []
    variances = {c: float(rcout[c].astype(float).std() or 0.0) for c in ch_cols}
    moving = [c for c, v in variances.items() if v > 5.0]
    moving.sort(key=lambda c: variances[c], reverse=True)
    return moving[:2] if moving else []


def _voltage_rolling_max(voltage: pd.DataFrame) -> pd.Series:
    """Plain time-windowed rolling max of voltage, no PWM-idle masking."""
    v = voltage.copy()
    v.index = pd.to_datetime(v['t_ns'], unit='ns')
    rolling_max = v['voltage'].rolling(f'{_V_OC_WINDOW_S}s').max()
    rolling_max.index = range(len(rolling_max))
    return rolling_max.ffill().bfill()


def _estimate_v_oc(
    voltage: pd.DataFrame, rcout: pd.DataFrame, thruster_cols: list[str],
) -> tuple[pd.Series, str]:
    """
    Estimate V_oc(t) = 90 s rolling max of idle-window voltage.

    Aligns voltage and rcout by nearest ``t_ns``, then masks voltage
    samples to those where every thruster channel sits in the idle
    band. The 90 s rolling-max over those idle samples (forward-filled
    across active periods) is the V_oc estimate.

    Returns
    -------
    (v_oc, method) where method is one of:
      - ``'idle-pwm-rolling-max'`` — primary method
      - ``'fallback-no-thruster-channels'`` — no movable RCOut channels
        found; used overall voltage rolling max
      - ``'fallback-no-idle-samples'`` — thruster channels never sat in
        the idle band; used overall voltage rolling max

    The fallbacks tend to *underestimate* V_oc (and thus current/power)
    because the rolling max sees voltage that's already drooping under
    load — surface this in the caller's warnings list.

    """
    if not thruster_cols:
        return _voltage_rolling_max(voltage), 'fallback-no-thruster-channels'

    merged = pd.merge_asof(
        voltage[['t_ns', 'voltage']].sort_values('t_ns'),
        rcout[['t_ns'] + thruster_cols].sort_values('t_ns'),
        on='t_ns', direction='nearest',
    )
    idle_mask = pd.Series(True, index=merged.index)
    for col in thruster_cols:
        v = merged[col].astype(float)
        idle_mask &= v.between(_IDLE_PWM_LOW, _IDLE_PWM_HIGH)
    if not idle_mask.any():
        return _voltage_rolling_max(voltage), 'fallback-no-idle-samples'

    merged['idle_voltage'] = np.where(idle_mask, merged['voltage'], np.nan)
    # Time-based rolling max so irregular sampling rates don't distort
    # the window.
    merged.index = pd.to_datetime(merged['t_ns'], unit='ns')
    rolling_max = (
        merged['idle_voltage']
        .rolling(f'{_V_OC_WINDOW_S}s').max()
        .ffill().bfill()
    )
    rolling_max.index = range(len(rolling_max))
    return rolling_max, 'idle-pwm-rolling-max'


def _segmented_energy_wh(
    t_s: np.ndarray, power: np.ndarray, gap_threshold_s: float = 5.0,
) -> float:
    """
    Trapezoidal energy integration that splits on recording gaps.

    On a multi-bag append, the combined timestamp series can have
    minute-or-more gaps between bags (rotation, recording stop). A
    plain ``np.trapz`` interpolates power linearly across that gap
    and adds fictitious Wh. Splitting at any ``dt > gap_threshold_s``
    avoids that. The 5 s default is well above normal sampling rates
    (battery is typically 10 Hz) but well below realistic bag-rotation
    gaps.
    """
    if len(t_s) < 2:
        return 0.0
    arr_t = np.asarray(t_s, dtype=float)
    arr_p = np.asarray(power, dtype=float)
    dt = np.diff(arr_t)
    breaks = np.where(dt > gap_threshold_s)[0]
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks + 1, [len(arr_t)]])
    total_ws = 0.0
    for s, e in zip(starts, ends):
        if e - s >= 2:
            total_ws += float(np.trapz(arr_p[s:e], arr_t[s:e]))
    return total_ws / 3600.0


def _resting_voltage(
    voltage: pd.DataFrame, t_target_ns: int, before: bool,
) -> float | None:
    """Mean voltage in a 30 s window just before/after t_target_ns."""
    if before:
        window = voltage[
            (voltage['t_ns'] >= t_target_ns - _RESTING_AVG_S * int(1e9))
            & (voltage['t_ns'] < t_target_ns)
        ]
    else:
        window = voltage[
            (voltage['t_ns'] >= t_target_ns)
            & (voltage['t_ns'] < t_target_ns + _RESTING_AVG_S * int(1e9))
        ]
    if window.empty:
        return None
    return float(window['voltage'].mean())


def _voltage_only_summary(
    bat_w: pd.DataFrame, start_v: float | None, end_v: float | None,
    window_label: str, extra_warning: str,
) -> list[str]:
    """Summary text for the rcout-absent fallback (voltage trace only)."""
    v = bat_w['voltage'].astype(float)
    summary = [
        '- battery: 2× Torqeedo Power 24-3500 LiFePO4 in parallel '
        f'({_CAPACITY_WH:.0f} Wh nominal)',
    ]
    if start_v is not None:
        summary.append(f'- start V (resting, pre-launch): {start_v:.2f} V')
    if end_v is not None:
        summary.append(f'- end V (resting, post-recovery): {end_v:.2f} V')
    if len(v):
        summary.append(
            f'- voltage {window_label}: range {v.min():.2f}–{v.max():.2f} V, '
            f'mean {v.mean():.2f} V'
        )
    summary.append(f'- {extra_warning}')
    return summary


def _render_voltage_only(
    bat_w: pd.DataFrame, t0: int, output_dir: Path, *,
    start_v: float | None, end_v: float | None,
    window_label: str, warning: str,
) -> PlotResult:
    """Render a single-pane voltage-only figure when rcout isn't available."""
    fig, ax = plt.subplots(figsize=(12, 4))
    elapsed = to_elapsed_s(bat_w['t_ns'], t0)
    ax.plot(elapsed, bat_w['voltage'].astype(float), linewidth=0.7)
    ax.set_ylabel('voltage (V)')
    ax.set_xlabel('elapsed time (s)')
    ax.grid(alpha=0.3)
    fig.suptitle(f'{TITLE} — {window_label} (voltage only)')
    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)
    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png,
        summary=_voltage_only_summary(
            bat_w, start_v, end_v, window_label, warning,
        ),
        warnings=[warning],
    )


def generate(
    db_path: Path, output_dir: Path, namespace: str,
) -> PlotResult:
    """Render voltage + V-drop-derived current/power, with summary stats."""
    meta = load_meta(db_path)
    t0 = meta['start_ns']
    launch_t_ns = meta.get('launch_t_ns')
    recovery_t_ns = meta.get('recovery_t_ns')

    battery = load_topic(db_path, topic('mavros/battery', namespace))
    rcout = load_topic(db_path, topic('mavros/rc/out', namespace))

    if battery is None or 'voltage' not in battery.columns:
        return PlotResult(
            plot_name=PLOT_NAME, title=TITLE,
            warnings=['battery voltage absent — power plot skipped'],
        )

    # Resting voltages: averaged across a small window straddling the
    # launch and recovery times (the boat is at rest pre-launch and
    # post-recovery, so V_oc ≈ V_load).
    start_v = (
        _resting_voltage(battery, launch_t_ns, before=True)
        if launch_t_ns is not None else None
    )
    end_v = (
        _resting_voltage(battery, recovery_t_ns, before=False)
        if recovery_t_ns is not None else None
    )

    # All downstream stats are computed against the in-water window
    # only (when known) so pre/post-crane idle voltage doesn't dilute
    # peak-stress numbers.
    if launch_t_ns is not None and recovery_t_ns is not None:
        bat_w = battery[
            (battery['t_ns'] >= launch_t_ns)
            & (battery['t_ns'] <= recovery_t_ns)
        ].reset_index(drop=True)
        rc_w = (
            rcout[
                (rcout['t_ns'] >= launch_t_ns)
                & (rcout['t_ns'] <= recovery_t_ns)
            ].reset_index(drop=True)
            if rcout is not None else None
        )
        window_label = 'in-water'
    else:
        bat_w = battery.reset_index(drop=True)
        rc_w = rcout.reset_index(drop=True) if rcout is not None else None
        window_label = 'full bag'

    if rc_w is None:
        return _render_voltage_only(
            bat_w, t0, output_dir,
            start_v=start_v, end_v=end_v, window_label=window_label,
            warning=('rc/out absent — V-drop current/power not '
                     'estimable; rendering voltage trace only'),
        )

    warnings: list[str] = []
    thruster_cols = _thruster_channels(rc_w)
    v_oc, v_oc_method = _estimate_v_oc(bat_w, rc_w, thruster_cols)
    if v_oc_method == 'fallback-no-thruster-channels':
        warnings.append(
            'no movable thruster RCOut channels detected; V_oc estimated '
            'from overall voltage rolling max (likely underestimates I/P)'
        )
    elif v_oc_method == 'fallback-no-idle-samples':
        warnings.append(
            'thrusters never sat in idle PWM band; V_oc estimated from '
            'overall voltage rolling max (likely underestimates I/P)'
        )

    v_load = bat_w['voltage'].astype(float).reset_index(drop=True)
    v_drop = (v_oc - v_load).clip(lower=0.0)
    current = (v_drop / _R_INT_OHMS)
    power = v_load * current

    # Energy integration: split on recording gaps so multi-bag append
    # boundaries don't add fictitious Wh.
    t_s = bat_w['t_ns'].astype('int64').to_numpy() / 1e9
    energy_wh = _segmented_energy_wh(t_s, power.to_numpy())
    pct_capacity = 100.0 * energy_wh / _CAPACITY_WH if _CAPACITY_WH else 0.0

    fig, axes = plt.subplots(3, 1, figsize=(12, 7), sharex=True)
    elapsed_b = to_elapsed_s(bat_w['t_ns'], t0)

    # Pane 1: voltage (loaded) and V_oc estimate
    axes[0].plot(elapsed_b, v_load, linewidth=0.7, label='V_load')
    axes[0].plot(
        elapsed_b, v_oc, linewidth=0.9, alpha=0.8,
        label='V_oc (90 s rolling max @ idle)',
    )
    axes[0].set_ylabel('voltage (V)')
    axes[0].grid(alpha=0.3)
    axes[0].legend(loc='lower left', fontsize=8)

    # Pane 2: estimated current
    axes[1].plot(elapsed_b, current, linewidth=0.5)
    axes[1].axhline(
        _PEAK_REF_CURRENT_A, color='r', linewidth=0.6,
        linestyle='--', alpha=0.6,
        label=f'2026-04-27 anchor: {_PEAK_REF_CURRENT_A:.0f} A',
    )
    axes[1].set_ylabel('current est. (A)')
    axes[1].grid(alpha=0.3)
    axes[1].legend(loc='upper left', fontsize=8)

    # Pane 3: estimated power
    axes[2].plot(elapsed_b, power, linewidth=0.5, color='C2')
    axes[2].set_ylabel('power est. (W)')
    axes[2].set_xlabel('elapsed time (s)')
    axes[2].grid(alpha=0.3)

    fig.suptitle(f'{TITLE} — {window_label}')
    fig.tight_layout()
    png = save_figure(fig, output_dir, PLOT_NAME)

    # Summary stats
    mean_v = float(v_load.mean()) if len(v_load) else float('nan')
    min_v = float(v_load.min()) if len(v_load) else float('nan')
    max_v = float(v_load.max()) if len(v_load) else float('nan')
    peak_i = float(current.max()) if len(current) else 0.0
    peak_p = float(power.max()) if len(power) else 0.0
    sag = (
        float(v_oc.iloc[v_load.idxmin()] - min_v)
        if len(v_load) and len(v_oc) else 0.0
    )

    summary = [
        '- battery: 2× Torqeedo Power 24-3500 LiFePO4 in parallel '
        f'({_CAPACITY_WH:.0f} Wh nominal)',
    ]
    if start_v is not None:
        summary.append(f'- start V (resting, pre-launch): {start_v:.2f} V')
    if end_v is not None:
        summary.append(f'- end V (resting, post-recovery): {end_v:.2f} V')
    summary += [
        f'- voltage {window_label}: range {min_v:.2f}–{max_v:.2f} V, '
        f'mean {mean_v:.2f} V',
        f'- max V sag (V_oc − V_load at min): {sag:.2f} V',
        f'- estimated peak current ({window_label}): {peak_i:.1f} A',
        f'- estimated peak power ({window_label}): {peak_p:.0f} W',
        f'- estimated energy used ({window_label}): {energy_wh:.0f} Wh '
        f'({pct_capacity:.1f}% of {_CAPACITY_WH:.0f} Wh nominal)',
        '- caveats: V-drop model anchored to a single 2026-04-27 '
        f'reference ({_PEAK_REF_CURRENT_A:.0f} A @ PWM '
        f'{_PEAK_REF_PWM}); R_int = {_R_INT_OHMS*1000:.1f} mΩ '
        'treated as constant; absolute values ±~20%, trends more '
        'reliable',
        '- LiFePO4 voltage-based SoC NOT reported — flat OCV curve '
        'across ~30–90% SoC makes it unreliable',
    ]

    return PlotResult(
        plot_name=PLOT_NAME, title=TITLE, png_path=png, summary=summary,
        warnings=warnings,
    )
