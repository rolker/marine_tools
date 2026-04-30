"""Report orchestrator: runs the configured plot tier and writes summary.md."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import matplotlib

# Headless backend; set before any pyplot import in the package.
# Plot modules import pyplot transitively, so configuring Agg here
# ensures CLI runs don't try to open a display.
matplotlib.use('Agg')

from .parquet_reader import load_index, load_meta  # noqa: E402
from .plots import TIER_1  # noqa: E402
from .plots._common import PlotResult  # noqa: E402


_TIERS = {1: TIER_1}


def render_report(
    parquet_dir: Path,
    output_dir: Path,
    *,
    namespace: str = 'bizzy',
    tier: int = 1,
) -> Path:
    """Run plot generators and write summary.md + PNGs.

    Returns the path of the written summary.md.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    plots = _TIERS.get(tier)
    if plots is None:
        raise ValueError(f'tier {tier} not implemented (only 1)')

    results: list[PlotResult] = [
        fn(parquet_dir, output_dir, namespace) for fn in plots
    ]

    summary_path = output_dir / 'summary.md'
    summary_path.write_text(
        _render_summary_md(parquet_dir, results, namespace),
    )
    return summary_path


def _render_summary_md(
    parquet_dir: Path,
    results: list[PlotResult],
    namespace: str,
) -> str:
    """Compose the markdown report from plot results + bag metadata."""
    meta = load_meta(parquet_dir)
    index = load_index(parquet_dir)
    start = datetime.fromtimestamp(meta['start_ns'] / 1e9, tz=timezone.utc)
    duration_s = meta['duration_ns'] / 1e9

    lines: list[str] = [
        f'# Bag analysis report — {Path(meta["source_bag_path"]).name}',
        '',
        '## Bag header',
        '',
        f'- **Source**: `{meta["source_bag_path"]}`',
        f'- **Start (UTC)**: {start.isoformat()}',
        f'- **Duration**: {duration_s:.1f} s ({duration_s / 60:.1f} min)',
        f'- **Topics**: {len(index)}',
        f'- **Total messages**: {meta["total_messages"]}',
        f'- **Robot namespace** (for plots): `{namespace}`',
        '',
    ]

    for r in results:
        lines.append(f'## {r.title}')
        lines.append('')
        if r.png_path is not None:
            lines.append(f'![{r.title}]({Path(r.png_path).name})')
            lines.append('')
        for line in r.summary:
            lines.append(line)
        if r.summary:
            lines.append('')
        if r.warnings:
            lines.append('**Warnings**:')
            for w in r.warnings:
                lines.append(f'- {w}')
            lines.append('')

    return '\n'.join(lines) + '\n'
