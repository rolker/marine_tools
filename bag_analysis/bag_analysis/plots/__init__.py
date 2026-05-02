"""
Tier-1 plot generators.

Each plot module exposes ``generate(db_path, output_dir, namespace)
-> PlotResult``, where ``db_path`` points to the SQLite database
written by ``bag_to_sqlite``. ``TIER_1`` pins the order in which
plots appear in the rendered ``summary.md``; new plots get added to
the appropriate tier list.

Matplotlib's Agg backend is selected here, before any plot module is
imported. Plot modules and their tests import ``matplotlib.pyplot``
directly at module load, so importing them on a headless host with no
DISPLAY would otherwise fail to find a usable backend. Selecting Agg
once at the package boundary keeps both the CLI flow (via
``report.py``) and direct test imports headless-safe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use('Agg')  # noqa: E402 (must precede the plot-module imports)

from . import (  # noqa: E402
    altitude_heave,
    comms,
    mode_timeline,
    power,
    sensor_health,
    speed_heading,
    track,
)
from ._common import PlotResult  # noqa: E402


PlotFn = Callable[[Path, Path, str], PlotResult]


TIER_1: list[PlotFn] = [
    mode_timeline.generate,
    track.generate,
    speed_heading.generate,
    power.generate,
    comms.generate,
    sensor_health.generate,
    altitude_heave.generate,
]
