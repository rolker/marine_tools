"""
SQLite writer for bag extractions.

One DB file per bag holds:
  - one table per topic, named ``t_<sanitized>``, with a ``t_ns``
    column plus one column per extractor field
  - ``_bag_meta`` (key, value) — bag header (start_ns, duration_ns,
    source_bag_path, total_messages); values stored as JSON strings
    so non-string types survive the round trip
  - ``_topic_index`` (topic, table_name, msg_type, count) — the
    inventory the reader uses to find tables by topic name

Schema is inferred per-topic from the row dicts via pandas.to_sql;
SQLite's loose typing absorbs minor inconsistencies. In-memory
buffering keeps the writer simple — for sub-GB bags it's fine; for
much larger bags we'd switch to streamed batch inserts.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


_TOPIC_SANITIZE_RE = re.compile(r'[^A-Za-z0-9_]+')


def topic_to_table(topic: str) -> str:
    """Sanitize a topic name into a SQLite table name.

    The ``t_`` prefix avoids leading-underscore tables and keeps
    user/topic tables visually distinct from the reserved
    ``_bag_meta`` / ``_topic_index`` tables.

    Examples
    --------
    >>> topic_to_table('/bizzy/mavros/battery')
    't_bizzy_mavros_battery'
    >>> topic_to_table('/diagnostics')
    't_diagnostics'
    """
    sanitized = _TOPIC_SANITIZE_RE.sub('_', topic).strip('_')
    return f't_{sanitized}'


class SqliteBagWriter:
    """Accumulate rows per topic and flush to one SQLite DB at finalize()."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._msg_types: dict[str, str] = {}

    def add(
        self,
        topic: str,
        msg_type: str,
        t_ns: int,
        fields: dict[str, Any],
    ) -> None:
        """Record one message: topic, type, timestamp, flattened fields."""
        row = {'t_ns': t_ns, **fields}
        self._rows[topic].append(row)
        self._msg_types[topic] = msg_type

    def finalize(
        self,
        *,
        source_bag_path: Path,
        start_ns: int,
        duration_ns: int,
        robot_namespace: str | None = None,
    ) -> dict[str, Any]:
        """Write per-topic tables + _bag_meta + _topic_index to the DB.

        Removes any existing DB file first so stale schema doesn't
        carry across runs. ``robot_namespace`` is recorded in
        ``_bag_meta`` so the report stage can default to the same
        namespace the extract was run for. Returns the bag-meta dict
        for caller logging.
        """
        if self.db_path.exists():
            self.db_path.unlink()

        index: dict[str, dict[str, Any]] = {}
        with sqlite3.connect(self.db_path) as conn:
            for topic, rows in self._rows.items():
                if not rows:
                    continue
                table = topic_to_table(topic)
                df = pd.DataFrame(rows)
                df.to_sql(table, conn, index=False, if_exists='replace')
                # Index t_ns for time-range queries during plot generation
                # and for ad-hoc use from the sqlite3 CLI.
                conn.execute(
                    f'CREATE INDEX idx_{table}_t_ns ON {table}(t_ns)',
                )
                index[topic] = {
                    'table_name': table,
                    'msg_type': self._msg_types[topic],
                    'count': len(rows),
                }

            meta: dict[str, Any] = {
                'source_bag_path': str(source_bag_path),
                'start_ns': start_ns,
                'duration_ns': duration_ns,
                'total_messages': sum(e['count'] for e in index.values()),
            }
            if robot_namespace is not None:
                meta['robot_namespace'] = robot_namespace

            conn.execute(
                'CREATE TABLE _bag_meta '
                '(key TEXT PRIMARY KEY, value TEXT)',
            )
            conn.executemany(
                'INSERT INTO _bag_meta(key, value) VALUES (?, ?)',
                [(k, json.dumps(v)) for k, v in meta.items()],
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
                'INSERT INTO _topic_index VALUES (?, ?, ?, ?)',
                [
                    (t, e['table_name'], e['msg_type'], e['count'])
                    for t, e in index.items()
                ],
            )
            conn.commit()
        return meta
