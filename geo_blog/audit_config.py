"""Fixed host calibration shared by article and competitor Lighthouse runs."""

from __future__ import annotations

import os
from typing import Any


def cpu_args() -> Any:
    """Read an explicit CPU benchmark setting for comparable optional audits."""
    value = float(os.environ.get("LIGHTHOUSE_CPU_SLOWDOWN_MULTIPLIER", "4"))
    if not 1 <= value <= 20:
        raise ValueError("Lighthouse CPU multiplier must be between 1 and 20")
    return [f"--throttling.cpuSlowdownMultiplier={value:g}"]
