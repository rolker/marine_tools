"""
Tests for the power plot.

Build a tiny SQLite extract by hand (no rosbag2 round trip) and exercise
the plot's load -> DataFrame -> matplotlib path end-to-end. Cases focus
on the new V-drop machinery: the rcout-missing fallback, the no-idle-
sample fallback, and the segmented energy integration.
"""

import json
from pathlib import Path
import sqlite3

from bag_analysis.plots.power import (
    _IDLE_PWM_HIGH,
    _segmented_energy_wh,
    generate,
)
import numpy as np


_START_NS = 1_700_000_000_000_000_000
_DURATION_NS = 60_000_000_000  # 60 s
_LAUNCH_NS = _START_NS + 5_000_000_000
_RECOVERY_NS = _START_NS + 55_000_000_000


def _open_extract_db(db_path: Path) -> sqlite3.Connection:
    """Create the empty bag_to_sqlite skeleton (meta + index tables)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        'CREATE TABLE _bag_meta (key TEXT PRIMARY KEY, value TEXT)',
    )
    conn.execute(
        'CREATE TABLE _topic_index ('
        '  topic TEXT PRIMARY KEY, '
        '  table_name TEXT, '
        '  msg_type TEXT, '
        '  count INTEGER'
        ')',
    )
    conn.executemany(
        'INSERT INTO _bag_meta(key, value) VALUES (?, ?)',
        [
            ('source_bag_paths', json.dumps(['/dev/null'])),
            ('start_ns', json.dumps(_START_NS)),
            ('duration_ns', json.dumps(_DURATION_NS)),
            ('total_messages', json.dumps(0)),
            ('launch_t_ns', json.dumps(_LAUNCH_NS)),
            ('recovery_t_ns', json.dumps(_RECOVERY_NS)),
        ],
    )
    return conn


def _write_battery(conn: sqlite3.Connection, voltages: list[float]) -> None:
    """Insert /bizzy/mavros/battery samples at 1 Hz starting at _START_NS."""
    conn.execute('CREATE TABLE t_bizzy_mavros_battery (t_ns INTEGER, voltage REAL)')
    rows = [
        (_START_NS + i * 1_000_000_000, v) for i, v in enumerate(voltages)
    ]
    conn.executemany(
        'INSERT INTO t_bizzy_mavros_battery VALUES (?, ?)', rows,
    )
    conn.execute(
        'INSERT INTO _topic_index VALUES (?, ?, ?, ?)',
        ('/bizzy/mavros/battery', 't_bizzy_mavros_battery',
         'sensor_msgs/msg/BatteryState', len(rows)),
    )


def _write_rcout(
    conn: sqlite3.Connection, channels: dict[str, list[int]],
) -> None:
    """Insert /bizzy/mavros/rc/out samples at 1 Hz with given channel PWM."""
    n = max(len(v) for v in channels.values())
    cols = sorted(channels.keys())
    cols_sql = ', '.join(f'{c} INTEGER' for c in cols)
    conn.execute(f'CREATE TABLE t_bizzy_mavros_rc_out (t_ns INTEGER, {cols_sql})')
    rows = [
        tuple([_START_NS + i * 1_000_000_000] + [channels[c][i] for c in cols])
        for i in range(n)
    ]
    placeholders = ', '.join(['?'] * (len(cols) + 1))
    conn.executemany(
        f'INSERT INTO t_bizzy_mavros_rc_out VALUES ({placeholders})', rows,
    )
    conn.execute(
        'INSERT INTO _topic_index VALUES (?, ?, ?, ?)',
        ('/bizzy/mavros/rc/out', 't_bizzy_mavros_rc_out',
         'mavros_msgs/msg/RCOut', n),
    )


def test_power_renders_with_voltage_and_idle_rcout(tmp_path):
    """End-to-end: voltage trace + RCOut with idle samples + load samples."""
    db_path = tmp_path / 'data.db'
    voltages = [28.0] * 10 + [25.0] * 50  # 10s idle, 50s under load
    ch_1 = [1500] * 10 + [1900] * 50  # idle, then loaded
    ch_3 = [1500] * 10 + [1900] * 50
    with _open_extract_db(db_path) as conn:
        _write_battery(conn, voltages)
        _write_rcout(conn, {'ch_1': ch_1, 'ch_3': ch_3})
        conn.commit()
    output_dir = tmp_path / 'report'

    result = generate(db_path, output_dir, namespace='bizzy')

    assert result.png_path is not None
    assert result.png_path.exists()
    assert any('estimated peak current' in line for line in result.summary)
    assert any('estimated energy used' in line for line in result.summary)
    # No fallback warnings expected when idle samples are present.
    assert all('fallback' not in (w or '') for w in (result.warnings or []))


def test_power_voltage_only_when_rcout_missing(tmp_path):
    """When rcout is absent, render voltage-only with a warning."""
    db_path = tmp_path / 'data.db'
    with _open_extract_db(db_path) as conn:
        _write_battery(conn, [28.0] * 60)
        conn.commit()
    output_dir = tmp_path / 'report'

    result = generate(db_path, output_dir, namespace='bizzy')

    # Plot still renders (voltage-only single pane).
    assert result.png_path is not None
    assert result.png_path.exists()
    # Warning surfaces the degradation.
    assert any('rc/out absent' in w for w in (result.warnings or []))
    # Summary should NOT include current/power/energy (those need rcout).
    assert not any(
        'estimated peak current' in line for line in result.summary
    )
    assert not any(
        'estimated energy used' in line for line in result.summary
    )


def test_power_warns_when_no_idle_samples(tmp_path):
    """Bags where thrusters never sit in idle should warn + fall back."""
    db_path = tmp_path / 'data.db'
    voltages = [25.0] * 60
    # Channels MUST vary (so _thruster_channels picks them) but never
    # enter the idle band [1480, 1520]. Alternate between two values
    # comfortably above idle.
    above_idle_a = _IDLE_PWM_HIGH + 100
    above_idle_b = _IDLE_PWM_HIGH + 300
    ch_1 = [above_idle_a if i % 2 else above_idle_b for i in range(60)]
    ch_3 = list(ch_1)
    with _open_extract_db(db_path) as conn:
        _write_battery(conn, voltages)
        _write_rcout(conn, {'ch_1': ch_1, 'ch_3': ch_3})
        conn.commit()
    output_dir = tmp_path / 'report'

    result = generate(db_path, output_dir, namespace='bizzy')

    assert result.png_path is not None
    # Either fallback ('no thruster channels' or 'no idle samples') is
    # acceptable; both indicate V_oc came from overall rolling max.
    assert any(
        'idle' in w.lower() or 'thruster' in w.lower()
        for w in (result.warnings or [])
    ), f'expected fallback warning, got: {result.warnings}'


def test_segmented_energy_skips_gaps():
    """A 2-segment trace with a 60 s gap integrates each segment only."""
    # Segment 1: t = 0..9 s, power = 100 W constant -> 9 s * 100 W = 900 Ws
    # GAP: t jumps from 9 to 70 (61 s gap, above 5 s threshold)
    # Segment 2: t = 70..79 s, power = 200 W constant -> 9 * 200 = 1800 Ws
    # Total: 2700 Ws / 3600 = 0.75 Wh
    t_s = np.array(
        list(range(10)) + [70 + i for i in range(10)], dtype=float,
    )
    power = np.array([100.0] * 10 + [200.0] * 10)
    wh = _segmented_energy_wh(t_s, power)
    assert abs(wh - 0.75) < 1e-6, f'expected 0.75 Wh, got {wh}'


def test_segmented_energy_handles_continuous_data():
    """No gap above threshold -> behaves like np.trapz / 3600."""
    t_s = np.linspace(0, 100, 101)  # 100 s, 1 Hz sampling
    power = np.full(101, 100.0)  # constant 100 W
    wh = _segmented_energy_wh(t_s, power)
    # 100 s * 100 W = 10_000 Ws = 10000/3600 ≈ 2.7778 Wh
    expected = 10_000 / 3600
    assert abs(wh - expected) < 1e-6


def test_segmented_energy_handles_short_inputs():
    """Single-point series can't be integrated; returns 0 cleanly."""
    assert _segmented_energy_wh(np.array([0.0]), np.array([100.0])) == 0.0
    assert _segmented_energy_wh(np.array([]), np.array([])) == 0.0
