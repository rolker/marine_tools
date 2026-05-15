"""
SQLite writer for bag extractions.

One DB file per *deployment* (one or more bags). Per-bag detail lives
in a `_bags` table; aggregate metadata in `_bag_meta`. Per-topic data
goes in `t_<sanitized>` tables, one row per message, with a `t_ns`
column plus one column per extractor field.

Schema is inferred per-topic from the row dicts via pandas.to_sql;
SQLite's loose typing absorbs minor inconsistencies. In `--append`
mode the existing tables are extended with new bags' rows; new
columns surfacing from a later bag are added via ALTER TABLE.

After every finalize (fresh or append) the launch/recovery detector
runs against the combined altitude data and records the in-water
window in `_bag_meta` so downstream queries / plots can default to
analysis-only-while-in-water.
"""

from __future__ import annotations

from collections import defaultdict
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any

import pandas as pd

from . import launch_recovery


_logger = logging.getLogger(__name__)


_TOPIC_SANITIZE_RE = re.compile(r'[^A-Za-z0-9_]+')


def topic_to_table(topic: str) -> str:
    """
    Sanitize a topic name into a SQLite table name.

    The ``t_`` prefix avoids leading-underscore tables and keeps
    user/topic tables visually distinct from the reserved
    ``_bag_meta`` / ``_topic_index`` / ``_bags`` tables.

    Examples
    --------
    >>> topic_to_table('/bizzy/mavros/battery')
    't_bizzy_mavros_battery'
    >>> topic_to_table('/diagnostics')
    't_diagnostics'

    """
    sanitized = _TOPIC_SANITIZE_RE.sub('_', topic).strip('_')
    return f't_{sanitized}'


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names of an existing SQLite table."""
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def _add_missing_columns(
    conn: sqlite3.Connection, table: str, df_cols: list[str],
) -> None:
    """ALTER TABLE to add any df columns not already in the existing table."""
    existing = _existing_columns(conn, table)
    for col in df_cols:
        if col not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}"')


class SqliteBagWriter:
    """
    Accumulate rows per topic and flush to a SQLite DB at finalize().

    Parameters
    ----------
    db_path
        Output database file.
    append
        If True, the DB must already exist; new rows are inserted into
        the existing per-topic tables (with ALTER TABLE for new
        columns), and a new row is added to ``_bags``. If False
        (default), any existing DB at ``db_path`` is removed first so
        stale schema doesn't carry across runs.

    """

    def __init__(self, db_path: Path, append: bool = False) -> None:
        """Open the writer; the DB file is created/extended on ``finalize()``."""
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._msg_types: dict[str, str] = {}
        self.append = append
        if append and not db_path.exists():
            raise FileNotFoundError(
                f'--append specified but DB does not exist: {db_path}. '
                f'Run without --append for the first bag.'
            )

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
        """
        Write per-topic tables + bag metadata + launch/recovery detection.

        Returns the bag-meta dict for caller logging.
        """
        if self.append:
            return self._finalize_append(
                source_bag_path=source_bag_path,
                start_ns=start_ns,
                duration_ns=duration_ns,
                robot_namespace=robot_namespace,
            )
        return self._finalize_fresh(
            source_bag_path=source_bag_path,
            start_ns=start_ns,
            duration_ns=duration_ns,
            robot_namespace=robot_namespace,
        )

    # ------------------------------------------------------------------
    # Fresh extraction (no existing DB)
    # ------------------------------------------------------------------

    def _finalize_fresh(
        self,
        *,
        source_bag_path: Path,
        start_ns: int,
        duration_ns: int,
        robot_namespace: str | None,
    ) -> dict[str, Any]:
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
                conn.execute(
                    f'CREATE INDEX idx_{table}_t_ns ON "{table}"(t_ns)',
                )
                index[topic] = {
                    'table_name': table,
                    'msg_type': self._msg_types[topic],
                    'count': len(rows),
                }

            self._create_meta_tables(conn)
            bag_idx = 0
            self._insert_bag_row(
                conn,
                bag_idx=bag_idx,
                source_bag_path=source_bag_path,
                start_ns=start_ns,
                duration_ns=duration_ns,
                msg_count=sum(e['count'] for e in index.values()),
            )
            self._write_topic_index(conn, index)

            meta: dict[str, Any] = {
                'bag_count': 1,
                'source_bag_paths': [str(source_bag_path)],
                'start_ns': start_ns,
                'end_ns': start_ns + duration_ns,
                'duration_ns': duration_ns,
                'total_messages': sum(e['count'] for e in index.values()),
            }
            if robot_namespace is not None:
                meta['robot_namespace'] = robot_namespace

            launch_t_ns, recovery_t_ns = launch_recovery.detect(
                conn, robot_namespace or 'bizzy',
            )
            meta['launch_t_ns'] = launch_t_ns
            meta['recovery_t_ns'] = recovery_t_ns

            self._write_meta(conn, meta)
            conn.commit()
        return meta

    # ------------------------------------------------------------------
    # Append to existing DB
    # ------------------------------------------------------------------

    def _finalize_append(
        self,
        *,
        source_bag_path: Path,
        start_ns: int,
        duration_ns: int,
        robot_namespace: str | None,
    ) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            # Detect legacy schema (pre-multi-bag DBs only have _bag_meta
            # and _topic_index — no _bags table). Reject up front rather
            # than fail mid-write with a raw "no such table: _bags" error.
            cur = conn.execute(
                'SELECT 1 FROM sqlite_master '
                "WHERE type='table' AND name='_bags'"
            )
            if cur.fetchone() is None:
                raise ValueError(
                    f'Legacy bag_analysis DB schema at {self.db_path}: '
                    f'missing _bags table. Pre-multi-bag extracts cannot '
                    f'be appended to directly. Re-extract without '
                    f'--append (delete the DB first), then append '
                    f'additional bags to the new DB.'
                )

            existing_meta = self._read_meta(conn)
            existing_namespace = existing_meta.get('robot_namespace')
            if (
                robot_namespace is not None
                and existing_namespace is not None
                and robot_namespace != existing_namespace
            ):
                raise ValueError(
                    f'--append: robot_namespace mismatch '
                    f'(existing={existing_namespace!r}, '
                    f'new={robot_namespace!r}). Cannot mix namespaces in one DB.'
                )
            if robot_namespace is not None:
                namespace = robot_namespace
            elif existing_namespace is not None:
                namespace = existing_namespace
            else:
                # No --robot-namespace flag and no stored value in the
                # DB. Defaulting to 'bizzy' will produce wrong topic-
                # table mappings on any other vehicle, and reports
                # generated from this DB will look authoritative
                # despite being wrong. Surface that loudly.
                namespace = 'bizzy'
                _logger.warning(
                    '--append on %s: no stored robot_namespace and no '
                    '--robot-namespace override; defaulting to %r. If '
                    'this DB is for a non-BizzyBoat platform, the '
                    'resulting topic-table mappings will be wrong. '
                    'Re-extract with --robot-namespace=<name> to fix.',
                    self.db_path, namespace,
                )

            existing_index = self._read_topic_index(conn)
            new_index = dict(existing_index)
            for topic, rows in self._rows.items():
                if not rows:
                    continue
                table = topic_to_table(topic)
                msg_type = self._msg_types[topic]
                df = pd.DataFrame(rows)

                if topic in existing_index:
                    if existing_index[topic]['msg_type'] != msg_type:
                        raise ValueError(
                            f'--append: msg_type mismatch on topic '
                            f'{topic} (existing='
                            f'{existing_index[topic]["msg_type"]!r}, '
                            f'new={msg_type!r})'
                        )
                    _add_missing_columns(conn, table, list(df.columns))
                    df.to_sql(table, conn, index=False, if_exists='append')
                    new_index[topic] = {
                        'table_name': table,
                        'msg_type': msg_type,
                        'count': existing_index[topic]['count'] + len(rows),
                    }
                else:
                    df.to_sql(table, conn, index=False, if_exists='replace')
                    conn.execute(
                        f'CREATE INDEX idx_{table}_t_ns ON "{table}"(t_ns)',
                    )
                    new_index[topic] = {
                        'table_name': table,
                        'msg_type': msg_type,
                        'count': len(rows),
                    }

            existing_bags = conn.execute(
                'SELECT COUNT(*) FROM _bags'
            ).fetchone()[0]
            self._insert_bag_row(
                conn,
                bag_idx=existing_bags,
                source_bag_path=source_bag_path,
                start_ns=start_ns,
                duration_ns=duration_ns,
                msg_count=sum(len(r) for r in self._rows.values()),
            )
            self._write_topic_index(conn, new_index, replace=True)

            bag_rows = conn.execute(
                'SELECT start_ns, duration_ns, source_path FROM _bags '
                'ORDER BY start_ns'
            ).fetchall()
            agg_start = min(r[0] for r in bag_rows)
            agg_end = max(r[0] + r[1] for r in bag_rows)
            agg_total = sum(e['count'] for e in new_index.values())

            meta: dict[str, Any] = {
                'bag_count': len(bag_rows),
                'source_bag_paths': [r[2] for r in bag_rows],
                'start_ns': agg_start,
                'end_ns': agg_end,
                'duration_ns': agg_end - agg_start,
                'total_messages': agg_total,
                'robot_namespace': namespace,
            }
            launch_t_ns, recovery_t_ns = launch_recovery.detect(
                conn, namespace,
            )
            meta['launch_t_ns'] = launch_t_ns
            meta['recovery_t_ns'] = recovery_t_ns

            self._write_meta(conn, meta)
            conn.commit()
        return meta

    # ------------------------------------------------------------------
    # Meta table helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _create_meta_tables(conn: sqlite3.Connection) -> None:
        conn.execute(
            'CREATE TABLE _bag_meta '
            '(key TEXT PRIMARY KEY, value TEXT)',
        )
        conn.execute(
            'CREATE TABLE _bags ('
            '  bag_idx INTEGER PRIMARY KEY, '
            '  source_path TEXT, '
            '  start_ns INTEGER, '
            '  duration_ns INTEGER, '
            '  msg_count INTEGER'
            ')',
        )
        conn.execute(
            'CREATE TABLE _topic_index ('
            '  topic TEXT PRIMARY KEY, '
            '  table_name TEXT, '
            '  msg_type TEXT, '
            '  count INTEGER'
            ')',
        )

    @staticmethod
    def _insert_bag_row(
        conn: sqlite3.Connection,
        *,
        bag_idx: int,
        source_bag_path: Path,
        start_ns: int,
        duration_ns: int,
        msg_count: int,
    ) -> None:
        conn.execute(
            'INSERT INTO _bags VALUES (?, ?, ?, ?, ?)',
            (bag_idx, str(source_bag_path), start_ns, duration_ns, msg_count),
        )

    @staticmethod
    def _write_topic_index(
        conn: sqlite3.Connection,
        index: dict[str, dict[str, Any]],
        replace: bool = False,
    ) -> None:
        if replace:
            conn.execute('DELETE FROM _topic_index')
        conn.executemany(
            'INSERT INTO _topic_index VALUES (?, ?, ?, ?)',
            [
                (t, e['table_name'], e['msg_type'], e['count'])
                for t, e in index.items()
            ],
        )

    @staticmethod
    def _read_topic_index(
        conn: sqlite3.Connection,
    ) -> dict[str, dict[str, Any]]:
        cur = conn.execute(
            'SELECT topic, table_name, msg_type, count FROM _topic_index'
        )
        return {
            row[0]: {'table_name': row[1], 'msg_type': row[2], 'count': row[3]}
            for row in cur.fetchall()
        }

    @staticmethod
    def _read_meta(conn: sqlite3.Connection) -> dict[str, Any]:
        cur = conn.execute('SELECT key, value FROM _bag_meta')
        return {row[0]: json.loads(row[1]) for row in cur.fetchall()}

    @staticmethod
    def _write_meta(
        conn: sqlite3.Connection, meta: dict[str, Any],
    ) -> None:
        conn.execute('DELETE FROM _bag_meta')
        conn.executemany(
            'INSERT INTO _bag_meta(key, value) VALUES (?, ?)',
            [(k, json.dumps(v)) for k, v in meta.items()],
        )
