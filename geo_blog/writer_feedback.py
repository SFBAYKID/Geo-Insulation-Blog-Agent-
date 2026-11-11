"""Measured correction context for bounded writer retries."""

from __future__ import annotations

import json
from typing import Any


def secondary_fits_title(primary: str, secondary: str) -> bool:
    """Keep standalone phrases readable within the existing title length limit."""
    if secondary.casefold() in primary.casefold() or primary.casefold() in secondary.casefold():
        return max(len(primary), len(secondary)) <= 55
    return len(primary) + len(secondary) + 2 <= 55


def structure_feedback(
    summary: dict[str, Any], metadata: dict[str, Any], primary: str, secondary: str
) -> str:
    """Give exact current values and measured limits without relaxing any checks."""
    details = {}
    for name, minimum, maximum in (("title", 1, 55), ("description", 140, 155)):
        value = metadata[name]
        details[name] = {
            "value": value,
            "characters": len(value),
            "minimum": minimum,
            "maximum": maximum,
            "characters_to_remove": max(0, len(value) - maximum),
            "characters_to_add": max(0, minimum - len(value)),
        }
    hint = ""
    if secondary and not secondary_fits_title(primary, secondary):
        hint = (
            "\nThese exact keyword phrases do not fit as separate phrases in a 55-character title. "
            "Use the primary phrase in the title; the secondary is still required in the H1, "
            "description, body and keyword metadata. Do not join them into an ungrammatical overlap."
        )
    return (
        "Fix every check marked failed without breaking any check marked passed.\n"
        + json.dumps(summary["checks"])
        + "\nMeasured metadata (counts exclude YAML quoting): "
        + json.dumps(details)
        + hint
    )
