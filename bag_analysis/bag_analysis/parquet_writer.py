"""
Parquet writer for bag extractions.

Accumulates per-topic rows in memory, then writes one parquet file per
topic plus index and metadata JSONs at finalize() time. Schema is
inferred by pyarrow from the row list; non-scalar fields fall through
as JSON strings via the extractor's fallback path.

Rationale for in-memory buffering vs streaming: bags in this workspace
are typically <1 GB with <a few million messages, which fits
comfortably in RAM. In-memory buffering avoids the streaming-schema-
inference dance. For larger bags we would switch to a per-topic
ParquetWriter with schema fixed from a sample batch.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


_TOPIC_SANITIZE_RE = re.compile(r'[^A-Za-z0-9_]+')


def topic_to_filename(topic: str) -> str:
    """Sanitize a topic name into a parquet filename.

    Examples
    --------
    >>> topic_to_filename('/bizzy/mavros/battery')
    '_bizzy_mavros_battery.parquet'
    >>> topic_to_filename('/diagnostics')
    '_diagnostics.parquet'
    """
    sanitized = _TOPIC_SANITIZE_RE.sub('_', topic)
    return f'{sanitized}.parquet'


class ParquetBagWriter:
    """Accumulate rows per topic and flush to parquet at finalize()."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
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
    ) -> dict[str, Any]:
        """Write per-topic parquet files plus _topic_index.json and _bag_meta.json.

        Returns the meta dict for caller convenience (e.g. logging).
        """
        index: dict[str, dict[str, Any]] = {}
        for topic, rows in self._rows.items():
            if not rows:
                continue
            filename = topic_to_filename(topic)
            table = pa.Table.from_pylist(rows)
            pq.write_table(table, self.output_dir / filename)
            index[topic] = {
                'file': filename,
                'msg_type': self._msg_types[topic],
                'count': len(rows),
            }

        with (self.output_dir / '_topic_index.json').open('w') as f:
            json.dump(index, f, indent=2, sort_keys=True)

        meta = {
            'source_bag_path': str(source_bag_path),
            'start_ns': start_ns,
            'duration_ns': duration_ns,
            'total_messages': sum(e['count'] for e in index.values()),
        }
        with (self.output_dir / '_bag_meta.json').open('w') as f:
            json.dump(meta, f, indent=2, sort_keys=True)
        return meta
