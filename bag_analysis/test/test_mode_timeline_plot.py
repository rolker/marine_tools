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
    assert any('mavros: 5' in line for line in result.summary)
    assert any('2 unique modes' in line for line in result.summary)


def test_mode_timeline_warns_when_no_topics_present(tmp_path):
    db_path = tmp_path / 'data.db'
    with _open_extract_db(db_path) as conn:
        conn.commit()
    output_dir = tmp_path / 'report'

    result = generate(db_path, output_dir, namespace='bizzy')

    assert result.png_path is None
    assert result.warnings, 'expected warnings when topics missing'
