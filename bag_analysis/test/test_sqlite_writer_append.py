"""
Tests for SqliteBagWriter --append namespace-fallback warning.

When --append targets a DB that has no stored robot_namespace and the
caller doesn't pass --robot-namespace, the writer silently defaults to
'bizzy'. For non-BizzyBoat platforms that silently produces wrong
topic-table mappings; the writer must surface that loudly so reports
generated from the DB don't look authoritative when they're wrong.
"""

import contextlib
import json
import logging
import sqlite3

from bag_analysis.sqlite_writer import SqliteBagWriter


def _make_legacy_compatible_db(db_path, *, with_namespace: bool):
    """
    Build a minimal DB with the schema --append expects to find.

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


class _ListHandler(logging.Handler):
    """Collect emitted log records into a list (test capture helper)."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextlib.contextmanager
def _capture_warnings(logger_name):
    """
    Collect WARNING+ records from `logger_name` via an attached handler.

    Deliberately does not use pytest's ``caplog``: that captures via a handler
    the pytest logging plugin installs on the *root* logger and relies on the
    record propagating there. On a minimal install (e.g. CI) that root handler
    can be absent, so the record falls through to ``logging.lastResort`` and
    ``caplog`` stays empty — making the warn assertion fail and the "silent"
    assertions pass vacuously. Attaching our own handler to the target logger
    is deterministic across environments.
    """
    logger = logging.getLogger(logger_name)
    handler = _ListHandler()
    prev_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


def test_append_warns_when_namespace_must_be_guessed(tmp_path):
    """No stored namespace + no --robot-namespace → loud warning, 'bizzy' fallback."""
    db_path = tmp_path / 'extract.sqlite'
    _make_legacy_compatible_db(db_path, with_namespace=False)

    writer = SqliteBagWriter(db_path, append=True)
    with _capture_warnings('bag_analysis.sqlite_writer') as records:
        meta = writer.finalize(
            source_bag_path=tmp_path / 'fake.bag',
            start_ns=1_700_000_060_000_000_000,
            duration_ns=60_000_000_000,
            robot_namespace=None,
        )

    assert meta['robot_namespace'] == 'bizzy'
    warning_text = '\n'.join(r.getMessage() for r in records)
    assert 'no stored robot_namespace' in warning_text, warning_text
    assert "defaulting to 'bizzy'" in warning_text, warning_text


def test_append_is_silent_when_namespace_explicit(tmp_path):
    """--robot-namespace passed → no warning fires (no guess needed)."""
    db_path = tmp_path / 'extract.sqlite'
    _make_legacy_compatible_db(db_path, with_namespace=False)

    writer = SqliteBagWriter(db_path, append=True)
    with _capture_warnings('bag_analysis.sqlite_writer') as records:
        meta = writer.finalize(
            source_bag_path=tmp_path / 'fake.bag',
            start_ns=1_700_000_060_000_000_000,
            duration_ns=60_000_000_000,
            robot_namespace='zebra',
        )

    assert meta['robot_namespace'] == 'zebra'
    assert all(
        'no stored robot_namespace' not in r.getMessage()
        for r in records
    ), [r.getMessage() for r in records]


def test_append_is_silent_when_namespace_stored(tmp_path):
    """DB has a stored namespace → no warning fires (no guess needed)."""
    db_path = tmp_path / 'extract.sqlite'
    _make_legacy_compatible_db(db_path, with_namespace=True)

    writer = SqliteBagWriter(db_path, append=True)
    with _capture_warnings('bag_analysis.sqlite_writer') as records:
        meta = writer.finalize(
            source_bag_path=tmp_path / 'fake.bag',
            start_ns=1_700_000_060_000_000_000,
            duration_ns=60_000_000_000,
            robot_namespace=None,
        )

    assert meta['robot_namespace'] == 'zebra'
    assert all(
        'no stored robot_namespace' not in r.getMessage()
        for r in records
    ), [r.getMessage() for r in records]
