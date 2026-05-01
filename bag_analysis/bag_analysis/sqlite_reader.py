"""
SQLite reader helpers used by plot modules.

Plot modules call ``load_topic(db_path, topic)`` to obtain a pandas
DataFrame; the ``_topic_index`` table abstracts away the table-name
sanitization. ``load_topic`` returns ``None`` (rather than raising)
when a topic is absent so plots can short-circuit cleanly when an
expected sensor was offline or the bag started before a node came up.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


def load_index(db_path: Path) -> dict[str, dict[str, Any]]:
    """Return _topic_index: ``{topic: {table_name, msg_type, count}}``."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            'SELECT topic, table_name, msg_type, count FROM _topic_index',
        ).fetchall()
    return {
        topic: {
            'table_name': table_name, 'msg_type': msg_type, 'count': count,
        }
        for topic, table_name, msg_type, count in rows
    }


def load_meta(db_path: Path) -> dict[str, Any]:
    """Return bag header from _bag_meta (JSON-decoded values)."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            'SELECT key, value FROM _bag_meta',
        ).fetchall()
    return {key: json.loads(value) for key, value in rows}


def load_topic(db_path: Path, topic: str) -> pd.DataFrame | None:
    """Load a topic's table as a DataFrame, or None if absent.

    A None return lets the caller short-circuit a plot when an expected
    topic isn't in the bag — strictly preferable to a try/except dance
    around a missing table. Rows are returned ordered by ``t_ns`` so
    plots get a time-sorted series; SQLite gives no order guarantee
    without an explicit ORDER BY, even though writes are time-ordered
    in practice.
    """
    index = load_index(db_path)
    entry = index.get(topic)
    if entry is None:
        return None
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql(
            f'SELECT * FROM {entry["table_name"]} ORDER BY t_ns',
            conn,
        )
