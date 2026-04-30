"""
Parquet reader helpers used by plot modules.

Plot modules call load_topic(...) to obtain a pandas DataFrame; the
index JSON keeps callers from having to know parquet filenames.
load_topic returns None (rather than raising) when a topic is absent
so plots can gracefully skip when an expected sensor was offline or
the bag started before a node came up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def load_index(parquet_dir: Path) -> dict[str, dict[str, Any]]:
    """Return _topic_index.json: {topic: {file, msg_type, count}}."""
    with (parquet_dir / '_topic_index.json').open() as f:
        return json.load(f)


def load_meta(parquet_dir: Path) -> dict[str, Any]:
    """Return _bag_meta.json: {source_bag_path, start_ns, duration_ns, total_messages}."""
    with (parquet_dir / '_bag_meta.json').open() as f:
        return json.load(f)


def load_topic(parquet_dir: Path, topic: str) -> pd.DataFrame | None:
    """Load a topic's parquet as a DataFrame, or None if absent.

    A None return lets the caller short-circuit a plot when an expected
    topic isn't in the bag — strictly preferable to a try/except dance.
    """
    index = load_index(parquet_dir)
    entry = index.get(topic)
    if entry is None:
        return None
    return pd.read_parquet(parquet_dir / entry['file'])
