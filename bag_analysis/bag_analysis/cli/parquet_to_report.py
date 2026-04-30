"""CLI: render a Tier-1 report from previously extracted parquet sidecars.

    ros2 run bag_analysis parquet_to_report \\
        --parquet-dir <dir> --output <out>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..report import render_report


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='parquet_to_report',
        description='Render a markdown bag report from parquet sidecars.',
    )
    p.add_argument(
        '--parquet-dir', required=True, type=Path,
        help='Directory produced by bag_to_parquet (with _topic_index.json)',
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


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = _build_parser().parse_args(argv)
    parquet_dir: Path = args.parquet_dir.resolve()
    output_dir: Path = args.output.resolve()

    if not (parquet_dir / '_topic_index.json').exists():
        print(
            f'error: {parquet_dir} does not look like a bag_to_parquet '
            f'output (no _topic_index.json found)',
            file=sys.stderr,
        )
        return 2

    summary = render_report(
        parquet_dir, output_dir,
        namespace=args.robot_namespace,
        tier=args.tier,
    )
    print(f'wrote {summary}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
