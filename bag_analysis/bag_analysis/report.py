"""
Report orchestrator: run the configured plot tier and write summary.md.

Importing ``.plots`` triggers ``plots/__init__.py`` which selects
matplotlib's Agg backend before any pyplot import, so this CLI flow
is headless-safe without further setup here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .plots import TIER_1
from .plots._common import PlotResult
from .sqlite_reader import load_index, load_meta


_TIERS = {1: TIER_1}


def render_report(
    db_path: Path,
    output_dir: Path,
    *,
    namespace: str = 'bizzy',
    tier: int = 1,
) -> Path:
    """
    Run plot generators and write summary.md + PNGs.

    Returns the path of the written summary.md.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    plots = _TIERS.get(tier)
    if plots is None:
        raise ValueError(f'tier {tier} not implemented (only 1)')

    results: list[PlotResult] = [
        fn(db_path, output_dir, namespace) for fn in plots
    ]

    summary_path = output_dir / 'summary.md'
    summary_path.write_text(
        _render_summary_md(db_path, results, namespace),
    )
    return summary_path


def _render_summary_md(
    db_path: Path,
    results: list[PlotResult],
    namespace: str,
) -> str:
    """Compose the markdown report from plot results + bag metadata."""
    meta = load_meta(db_path)
    index = load_index(db_path)
    start = datetime.fromtimestamp(meta['start_ns'] / 1e9, tz=timezone.utc)
    duration_s = meta['duration_ns'] / 1e9
    bag_paths: list[str] = meta.get('source_bag_paths', []) or []
    bag_count = meta.get('bag_count', 1)

    if bag_count > 1:
        title = f'Bag analysis report — {bag_count} bags'
    elif bag_paths:
        title = f'Bag analysis report — {Path(bag_paths[0]).name}'
    else:
        title = 'Bag analysis report'

    lines: list[str] = [
        f'# {title}',
        '',
        '## Bag header',
        '',
    ]
    if bag_count > 1:
        lines.append(f'- **Bags**: {bag_count}')
        for p in bag_paths:
            lines.append(f'  - `{p}`')
    elif bag_paths:
        lines.append(f'- **Source**: `{bag_paths[0]}`')
    lines += [
        f'- **Start (UTC)**: {start.isoformat()}',
        f'- **Duration**: {duration_s:.1f} s ({duration_s / 60:.1f} min)',
        f'- **Topics**: {len(index)}',
        f'- **Total messages**: {meta["total_messages"]}',
        f'- **Robot namespace** (for plots): `{namespace}`',
    ]
    launch_t_ns = meta.get('launch_t_ns')
    recovery_t_ns = meta.get('recovery_t_ns')
    if launch_t_ns is not None and recovery_t_ns is not None:
        in_water_min = (recovery_t_ns - launch_t_ns) / 1e9 / 60
        launch_dt = datetime.fromtimestamp(launch_t_ns / 1e9, tz=timezone.utc)
        recovery_dt = datetime.fromtimestamp(recovery_t_ns / 1e9, tz=timezone.utc)
        lines += [
            f'- **In-water window**: {launch_dt.isoformat()} → '
            f'{recovery_dt.isoformat()} ({in_water_min:.1f} min)',
        ]
    lines.append('')

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
