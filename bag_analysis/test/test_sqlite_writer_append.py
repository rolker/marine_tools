"""Tests for SqliteBagWriter --append namespace-fallback warning.

When --append targets a DB that has no stored robot_namespace and the
caller doesn't pass --robot-namespace, the writer silently defaults to
'bizzy'. For non-BizzyBoat platforms that silently produces wrong
topic-table mappings; the writer must surface that loudly so reports
generated from the DB don't look authoritative when they're wrong.
"""

import json
import logging
import sqlite3

from bag_analysis.sqlite_writer import SqliteBagWriter


def _make_legacy_compatible_db(db_path, *, with_namespace: bool):
    """Build a minimal DB with the schema --append expects to find.

    Includes `_bags` (the marker that distinguishes new vs pre-multi-bag
    schema), `_bag_meta`, and an empty `_topic_index`. If
    `with_namespace` is True, also stores robot_namespace='zebra'.
    """
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE _bags ('
                 'bag_idx INTEGER PRIMARY KEY, source_path TEXT, '
                 'start_ns INTEGER, duration_ns INTEGER, msg_count INTEGER)')
    conn.execute('CREATE TABLE _bag_meta (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute('CREATE TABLE _topic_index ('
                 'topic TEXT PRIMARY KEY, table_name TEXT, '
                 'msg_type TEXT, count INTEGER)')
    seed_rows = [
        ('source_bag_paths', json.dumps(['/dev/null'])),
        ('start_ns', json.dumps(1_700_000_000_000_000_000)),
        ('duration_ns', json.dumps(60_000_000_000)),
        ('total_messages', json.dumps(0)),
    ]
    if with_namespace:
        seed_rows.append(('robot_namespace', json.dumps('zebra')))
    conn.executemany(
        'INSERT INTO _bag_meta(key, value) VALUES (?, ?)', seed_rows,
    )
    conn.commit()
    conn.close()


def test_append_warns_when_namespace_must_be_guessed(tmp_path, caplog):
    """No stored namespace + no --robot-namespace → loud warning, 'bizzy' fallback."""
    db_path = tmp_path / 'extract.sqlite'
    _make_legacy_compatible_db(db_path, with_namespace=False)

    writer = SqliteBagWriter(db_path, append=True)
    with caplog.at_level(logging.WARNING, logger='bag_analysis.sqlite_writer'):
        meta = writer.finalize(
            source_bag_path=tmp_path / 'fake.bag',
            start_ns=1_700_000_060_000_000_000,
            duration_ns=60_000_000_000,
            robot_namespace=None,
        )

    assert meta['robot_namespace'] == 'bizzy'
    warning_text = '\n'.join(r.message for r in caplog.records)
    assert 'no stored robot_namespace' in warning_text, warning_text
    assert "defaulting to 'bizzy'" in warning_text, warning_text


def test_append_is_silent_when_namespace_explicit(tmp_path, caplog):
    """--robot-namespace passed → no warning fires (no guess needed)."""
    db_path = tmp_path / 'extract.sqlite'
    _make_legacy_compatible_db(db_path, with_namespace=False)

    writer = SqliteBagWriter(db_path, append=True)
    with caplog.at_level(logging.WARNING, logger='bag_analysis.sqlite_writer'):
        meta = writer.finalize(
            source_bag_path=tmp_path / 'fake.bag',
            start_ns=1_700_000_060_000_000_000,
            duration_ns=60_000_000_000,
            robot_namespace='zebra',
        )

    assert meta['robot_namespace'] == 'zebra'
    assert all(
        'no stored robot_namespace' not in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


def test_append_is_silent_when_namespace_stored(tmp_path, caplog):
    """DB has a stored namespace → no warning fires (no guess needed)."""
    db_path = tmp_path / 'extract.sqlite'
    _make_legacy_compatible_db(db_path, with_namespace=True)

    writer = SqliteBagWriter(db_path, append=True)
    with caplog.at_level(logging.WARNING, logger='bag_analysis.sqlite_writer'):
        meta = writer.finalize(
            source_bag_path=tmp_path / 'fake.bag',
            start_ns=1_700_000_060_000_000_000,
            duration_ns=60_000_000_000,
            robot_namespace=None,
        )

    assert meta['robot_namespace'] == 'zebra'
    assert all(
        'no stored robot_namespace' not in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]
