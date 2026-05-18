"""
Tests for the mode_timeline plot.

Build a tiny SQLite extract by hand (no rosbag2 round trip) and exercise
the plot's load → DataFrame → matplotlib path end-to-end. A real bag
fixture would dwarf this by orders of magnitude for the same assertions.
"""

import json
from pathlib import Path
import sqlite3

from bag_analysis.plots.mode_timeline import generate


_START_NS = 1_700_000_000_000_000_000
_DURATION_NS = 60_000_000_000  # 60 s


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
            ('source_bag_path', json.dumps('/dev/null')),
            ('start_ns', json.dumps(_START_NS)),
            ('duration_ns', json.dumps(_DURATION_NS)),
            ('total_messages', json.dumps(0)),
        ],
    )
    return conn


def _write_state_topic(conn: sqlite3.Connection) -> None:
    """Insert a synthetic /bizzy/mavros/state table + index entry."""
    conn.execute("""
        CREATE TABLE t_bizzy_mavros_state (
            t_ns INTEGER,
            mode TEXT,
            armed INTEGER,
            connected INTEGER
        )
    """)
    rows = [
        (_START_NS + i * 1_000_000_000,
         mode,
         int(armed),
         int(True))
        for i, (mode, armed) in enumerate([
            ('MANUAL', False),
            ('MANUAL', False),
            ('AUTO', True),
            ('AUTO', True),
            ('MANUAL', False),
        ])
    ]
    conn.executemany(
        'INSERT INTO t_bizzy_mavros_state VALUES (?, ?, ?, ?)', rows,
    )
    conn.execute(
        'INSERT INTO _topic_index VALUES (?, ?, ?, ?)',
        ('/bizzy/mavros/state', 't_bizzy_mavros_state',
         'mavros_msgs/msg/State', 5),
    )


def test_mode_timeline_renders_when_state_present(tmp_path):
    db_path = tmp_path / 'data.db'
    with _open_extract_db(db_path) as conn:
        _write_state_topic(conn)
        conn.commit()
    output_dir = tmp_path / 'report'

    result = generate(db_path, output_dir, namespace='bizzy')

    assert result.png_path is not None
    assert result.png_path.exists()
    assert result.png_path.suffix == '.png'
    assert any('FCU mode: 5' in line for line in result.summary)
    assert any('2 unique modes' in line for line in result.summary)


def test_mode_timeline_warns_when_no_topics_present(tmp_path):
    db_path = tmp_path / 'data.db'
    with _open_extract_db(db_path) as conn:
        conn.commit()
    output_dir = tmp_path / 'report'

    result = generate(db_path, output_dir, namespace='bizzy')

    assert result.png_path is None
    assert result.warnings, 'expected warnings when topics missing'


def _write_heartbeat_with_piloting_mode(conn: sqlite3.Connection) -> None:
    """Insert /bizzy/marine/heartbeat with populated piloting_mode."""
    conn.execute("""
        CREATE TABLE t_bizzy_marine_heartbeat (
            t_ns INTEGER,
            frame_id TEXT,
            header_t_ns INTEGER,
            piloting_mode TEXT,
            marine_autonomy_standby TEXT
        )
    """)
    rows = [
        (_START_NS + i * 1_000_000_000, '', _START_NS + i * 1_000_000_000,
         mode, standby)
        for i, (mode, standby) in enumerate([
            ('Standby', 'true'),
            ('Standby', 'true'),
            ('Autonomous', 'false'),
            ('Autonomous', 'false'),
            ('Manual', 'false'),
        ])
    ]
    conn.executemany(
        'INSERT INTO t_bizzy_marine_heartbeat VALUES (?, ?, ?, ?, ?)', rows,
    )
    conn.execute(
        'INSERT INTO _topic_index VALUES (?, ?, ?, ?)',
        ('/bizzy/marine/heartbeat', 't_bizzy_marine_heartbeat',
         'marine_interfaces/msg/Heartbeat', 5),
    )


def _write_mission_manager_with_current_nav_task(
    conn: sqlite3.Connection,
) -> None:
    """Insert mission_manager heartbeat with populated current_nav_task."""
    conn.execute("""
        CREATE TABLE t_bizzy_marine_status_mission_manager (
            t_ns INTEGER,
            frame_id TEXT,
            header_t_ns INTEGER,
            navigator TEXT,
            current_nav_task TEXT
        )
    """)
    rows = [
        (_START_NS + i * 1_000_000_000, '', 0, 'active', task)
        for i, task in enumerate([
            'hover_override',
            'hover_override',
            'trackline0000',
            'trackline0000',
            'done_hover',
        ])
    ]
    conn.executemany(
        'INSERT INTO t_bizzy_marine_status_mission_manager '
        'VALUES (?, ?, ?, ?, ?)', rows,
    )
    conn.execute(
        'INSERT INTO _topic_index VALUES (?, ?, ?, ?)',
        ('/bizzy/marine/status/mission_manager',
         't_bizzy_marine_status_mission_manager',
         'marine_interfaces/msg/Heartbeat', 5),
    )


def test_mode_timeline_renders_piloting_state_lane(tmp_path):
    db_path = tmp_path / 'data.db'
    with _open_extract_db(db_path) as conn:
        _write_heartbeat_with_piloting_mode(conn)
        conn.commit()
    output_dir = tmp_path / 'report'

    result = generate(db_path, output_dir, namespace='bizzy')

    assert result.png_path is not None
    assert any('piloting state:' in line for line in result.summary)
    # 3 unique modes (Standby, Autonomous, Manual)
    assert any('3 unique' in line for line in result.summary)


def test_mode_timeline_renders_current_task_lane(tmp_path):
    db_path = tmp_path / 'data.db'
    with _open_extract_db(db_path) as conn:
        _write_mission_manager_with_current_nav_task(conn)
        conn.commit()
    output_dir = tmp_path / 'report'

    result = generate(db_path, output_dir, namespace='bizzy')

    assert result.png_path is not None
    assert any('current task:' in line for line in result.summary)
    # 3 unique tasks (hover_override, trackline0000, done_hover)
    assert any('3 unique' in line for line in result.summary)


def test_mode_timeline_trims_to_in_water_window(tmp_path):
    """
    Verify in-water window trimming.

    When _bag_meta has launch_t_ns/recovery_t_ns, rows outside the
    window are excluded from the per-lane summary counts.
    """
    db_path = tmp_path / 'data.db'
    with _open_extract_db(db_path) as conn:
        # Set a tight in-water window covering only rows i=1..3 of each
        # 5-row table (t_ns = _START_NS + 1e9 .. _START_NS + 3e9).
        launch = _START_NS + 1_000_000_000
        recovery = _START_NS + 3_000_000_000
        conn.executemany(
            'INSERT INTO _bag_meta(key, value) VALUES (?, ?)',
            [
                ('launch_t_ns', json.dumps(launch)),
                ('recovery_t_ns', json.dumps(recovery)),
            ],
        )
        _write_state_topic(conn)  # 5 rows
        conn.commit()
    output_dir = tmp_path / 'report'

    result = generate(db_path, output_dir, namespace='bizzy')

    assert result.png_path is not None
    # 3 rows fall inside the window (indices 1, 2, 3)
    assert any('FCU mode: 3' in line for line in result.summary)
