"""
Tier-1 plot generators.

Each plot module exposes ``generate(parquet_dir, output_dir, namespace)
-> PlotResult``. ``TIER_1`` pins the order in which plots appear in
the rendered ``summary.md``; new plots get added to the appropriate
tier list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import (
    altitude_heave,
    comms,
    mode_timeline,
    power,
    sensor_health,
    speed_heading,
    track,
)
from ._common import PlotResult


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
