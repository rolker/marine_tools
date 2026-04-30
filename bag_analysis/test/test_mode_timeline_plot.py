"""Tests for the mode_timeline plot.

These tests build a tiny parquet directory by hand (no rosbag2 round
trip) and exercise the plot's load → DataFrame → matplotlib path
end-to-end. A real bag fixture would dwarf the test by orders of
magnitude for the same assertion power.
"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from bag_analysis.plots.mode_timeline import generate


_START_NS = 1_700_000_000_000_000_000
_DURATION_NS = 60_000_000_000  # 60s


def _write_meta(parquet_dir: Path, *, total_messages: int) -> None:
    (parquet_dir / '_bag_meta.json').write_text(json.dumps({
        'source_bag_path': '/dev/null',
        'start_ns': _START_NS,
        'duration_ns': _DURATION_NS,
        'total_messages': total_messages,
    }))


def _write_state_parquet(parquet_dir: Path) -> None:
    """Write a synthetic /bizzy/mavros/state parquet + index entry."""
    table = pa.table({
        't_ns': [_START_NS + i * 1_000_000_000 for i in range(5)],
        'frame_id': [''] * 5,
        'header_t_ns': [_START_NS + i * 1_000_000_000 for i in range(5)],
        'connected': [True] * 5,
        'armed': [False, False, True, True, False],
        'guided': [False, False, True, True, False],
        'manual_input': [True, True, False, False, True],
        'mode': ['MANUAL', 'MANUAL', 'AUTO', 'AUTO', 'MANUAL'],
        'system_status': [3] * 5,
    })
    pq.write_table(table, parquet_dir / '_bizzy_mavros_state.parquet')


def test_mode_timeline_renders_when_state_present(tmp_path):
    parquet_dir = tmp_path / 'parquet'
    parquet_dir.mkdir()
    _write_state_parquet(parquet_dir)
    (parquet_dir / '_topic_index.json').write_text(json.dumps({
        '/bizzy/mavros/state': {
            'file': '_bizzy_mavros_state.parquet',
            'msg_type': 'mavros_msgs/msg/State',
            'count': 5,
        },
    }))
    _write_meta(parquet_dir, total_messages=5)
    output_dir = tmp_path / 'report'

    result = generate(parquet_dir, output_dir, namespace='bizzy')

    assert result.png_path is not None
    assert result.png_path.exists()
    assert result.png_path.suffix == '.png'
    assert any('mavros: 5' in line for line in result.summary)
    assert any('2 unique modes' in line for line in result.summary)


def test_mode_timeline_warns_when_no_topics_present(tmp_path):
    parquet_dir = tmp_path / 'parquet'
    parquet_dir.mkdir()
    (parquet_dir / '_topic_index.json').write_text('{}')
    _write_meta(parquet_dir, total_messages=0)
    output_dir = tmp_path / 'report'

    result = generate(parquet_dir, output_dir, namespace='bizzy')

    assert result.png_path is None
    assert result.warnings, 'expected at least one warning when topics missing'
