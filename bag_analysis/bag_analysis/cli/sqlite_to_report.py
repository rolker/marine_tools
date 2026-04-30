"""CLI: render a Tier-1 report from a previously extracted SQLite DB.

    ros2 run bag_analysis sqlite_to_report \\
        --db <data.db> --output <out>
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from ..report import render_report


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='sqlite_to_report',
        description='Render a markdown bag report from a SQLite extract.',
    )
    p.add_argument(
        '--db', required=True, type=Path,
        help='SQLite database produced by bag_to_sqlite',
    )
    p.add_argument(
        '--output', required=True, type=Path,
        help='Output directory for summary.md + PNG plots',
    )
    p.add_argument(
        '--robot-namespace', default='bizzy',
        help='Robot namespace prefix for plots (default: bizzy)',
    )
    p.add_argument(
        '--tier', type=int, default=1,
        help='Plot tier to render (default: 1; only tier 1 is implemented)',
    )
    return p


def _looks_like_extract_db(db_path: Path) -> bool:
    """Sanity-check the DB has the bag_to_sqlite schema."""
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            tables = {
                name for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
        return '_bag_meta' in tables and '_topic_index' in tables
    except sqlite3.DatabaseError:
        return False


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = _build_parser().parse_args(argv)
    db_path: Path = args.db.resolve()
    output_dir: Path = args.output.resolve()

    if not _looks_like_extract_db(db_path):
        print(
            f'error: {db_path} does not look like a bag_to_sqlite output '
            f'(missing _bag_meta or _topic_index)',
            file=sys.stderr,
        )
        return 2

    summary = render_report(
        db_path, output_dir,
        namespace=args.robot_namespace,
        tier=args.tier,
    )
    print(f'wrote {summary}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
