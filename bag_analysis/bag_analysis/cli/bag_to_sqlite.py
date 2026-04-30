"""CLI: extract a rosbag2 to a SQLite database.

    ros2 run bag_analysis bag_to_sqlite --bag <bag-dir> --output <db-path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..extractors import extract
from ..reader import iter_messages, open_reader
from ..sqlite_writer import SqliteBagWriter


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='bag_to_sqlite',
        description='Extract a rosbag2 directory to a SQLite database.',
    )
    p.add_argument(
        '--bag', required=True, type=Path,
        help='Path to the rosbag2 directory (the one with metadata.yaml)',
    )
    p.add_argument(
        '--output', required=True, type=Path,
        help='Output SQLite database file path (e.g. .../data.db)',
    )
    p.add_argument(
        '--topics', nargs='+', default=None,
        help='Whitelist of topics; default: all topics in the bag',
    )
    p.add_argument(
        '--robot-namespace', default='bizzy',
        help=('Robot namespace (default: bizzy). Currently informational '
              '— extraction is namespace-agnostic; the value is recorded '
              'in the meta table for downstream report generation.'),
    )
    return p


def _bag_time_bounds(bag_path: Path) -> tuple[int, int]:
    """Return (start_ns, duration_ns) from the bag's rosbag2 metadata."""
    reader, _ = open_reader(bag_path)
    meta = reader.get_metadata()
    return int(meta.starting_time.nanoseconds), int(meta.duration.nanoseconds)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = _build_parser().parse_args(argv)
    bag_path: Path = args.bag.resolve()
    db_path: Path = args.output.resolve()

    if not bag_path.exists():
        print(f'error: bag path not found: {bag_path}', file=sys.stderr)
        return 2

    start_ns, duration_ns = _bag_time_bounds(bag_path)

    # Re-open via a probe to enumerate types; iter_messages opens its
    # own reader, so we drop this one after the type lookup.
    probe_reader, topic_types = open_reader(bag_path)
    del probe_reader

    print(f'extracting {bag_path}', flush=True)
    print(f'  -> {db_path}', flush=True)
    print(f'  topics in bag: {len(topic_types)}', flush=True)

    writer = SqliteBagWriter(db_path)
    n = 0
    for topic, msg, t_ns in iter_messages(bag_path, topics=args.topics):
        msg_type = topic_types[topic]
        writer.add(topic, msg_type, t_ns, extract(msg_type, msg))
        n += 1
        if n % 50000 == 0:
            print(f'  ... {n} msgs processed', flush=True)

    meta = writer.finalize(
        source_bag_path=bag_path,
        start_ns=start_ns,
        duration_ns=duration_ns,
    )
    print(
        f'done: {meta["total_messages"]} messages written, '
        f'output at {db_path}',
        flush=True,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
