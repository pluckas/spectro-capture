#!/usr/bin/env python3
# Spectro Capture
# © Paul Luckas, 2025
#
# This file is part of the Spectro Capture project.
# Licensed for personal, educational, and research use only.
# Commercial use is not permitted without prior permission.
#
# See the LICENSE file for full terms.

"""
scheduler.py
------------
Pure scheduling/selection logic for Batch Runner.

Given a list of ObservingBlocks, chooses the next suitable block based on
hour-angle window rules. No hardware calls, no GUI calls, no sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Tuple

from utils import hour_angle_hours


@dataclass
class Target:
    name: str
    ra_hours: float          # decimal hours
    dec_deg: float           # decimal degrees
    exp_s: float
    frames: int
    calibrate: bool = False  # only meaningful for science targets


@dataclass
class ObservingBlock:
    science: Target
    reference: Optional[Target] = None
    ref_order: str = "before"   # "before" or "after"
    completed: bool = False
    attempts: int = 0
    last_attempt_utc: Optional[datetime] = None


# ============================================================
# Core scheduler
# ============================================================
def choose_next_block(
    blocks: List[ObservingBlock],
    when_utc: Optional[datetime] = None,
    ha_min: float = -1.0,
    ha_max: float = 2.0,
) -> Optional[Tuple[ObservingBlock, float]]:
    """
    Returns (block, HA_hours) for the chosen block, or None if none suitable.
    HA is computed from the SCIENCE target only.
    """
    if when_utc is None:
        when_utc = datetime.now(timezone.utc)

    eligible: List[Tuple[ObservingBlock, float]] = []

    for b in blocks:
        if b.completed:
            continue

        ha = hour_angle_hours(b.science.ra_hours, when_utc)
        if ha_min <= ha <= ha_max:
            eligible.append((b, ha))

    if not eligible:
        return None

    east = [(b, ha) for (b, ha) in eligible if ha >= 0.0]
    west = [(b, ha) for (b, ha) in eligible if ha < 0.0]

    if east:
        # smallest positive HA (closest to transit, still east side)
        return min(east, key=lambda x: x[1])
    else:
        # most negative HA (west-most, but still within window)
        return max(west, key=lambda x: x[1])


# ============================================================
# Index-based helper for Batch Runner
# ============================================================
def choose_next_block_index(
    blocks: List[ObservingBlock],
    when_utc: Optional[datetime] = None,
    ha_min: float = -1.0,
    ha_max: float = 2.0,
) -> Optional[int]:
    """
    Returns the INDEX of the chosen ObservingBlock in `blocks`,
    or None if no block is suitable.
    """
    result = choose_next_block(blocks, when_utc, ha_min, ha_max)
    if result is None:
        return None

    chosen_block, _ha = result
    return blocks.index(chosen_block)
