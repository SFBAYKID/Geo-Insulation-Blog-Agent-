"""Measured correction context and keyword-fit rules for bounded writer retries.

Why this exists: a topic's exact keyword pair can be physically impossible to place
inside a length-limited field. The November 4, 2026 attic topic needed 59 characters
for a 55-character title, and the November 11 cellulose topic needed 77 for a
70-character H1. Each burned seven paid attempts against a rule no draft could
satisfy. Every field limit and its keyword requirement now come from one table, so
validation, writer feedback and topic selection cannot disagree.
"""

from __future__ import annotations

import json
from typing import Any

from .text_format import count_keyword_in_text

# Hard website limits, matched by astro_export before any build.
TITLE_MAX = 55
H1_MAX = 70
DESCRIPTION_MIN = 140
DESCRIPTION_MAX = 155
# Characters left for natural wording once both phrases are in the description.
# Below this the writer is fitting a puzzle, not writing a summary.
DESCRIPTION_WORDING_ROOM = 35
SEPARATOR = 2  # e.g. "? " or ": " between two standalone phrases

SECONDARY_FIELD_LIMITS = {
    "title": TITLE_MAX,
    "h1": H1_MAX,
    "description": DESCRIPTION_MAX - DESCRIPTION_WORDING_ROOM,
}


def phrases_length(primary: str, secondary: str) -> int:
    """Shortest readable length holding both exact phrases, without merging them."""
    p, s = primary.strip(), secondary.strip()
    if count_keyword_in_text(p, s) or count_keyword_in_text(s, p):
        return max(len(p), len(s))
    return len(p) + SEPARATOR + len(s)


def secondary_required_in(field: str, primary: str, secondary: str) -> bool:
    """Require the supporting phrase in a limited field only when both phrases fit."""
    return bool(secondary.strip()) and (
        phrases_length(primary, secondary) <= SECONDARY_FIELD_LIMITS[field]
    )


def secondary_fits_title(primary: str, secondary: str) -> bool:
    """Keep standalone phrases readable within the existing title length limit."""
    return secondary_required_in("title", primary, secondary)


def exempt_secondary_fields(primary: str, secondary: str) -> list[str]:
    """Fields where the supporting phrase is waived because the pair cannot fit."""
    if not secondary.strip():
        return []
    return [f for f in SECONDARY_FIELD_LIMITS if not secondary_required_in(f, primary, secondary)]


def topic_blockers(primary: str) -> list[str]:
    """Reasons no draft can pass, found before any paid model call.

    After the supporting-phrase waivers, the only placement that can still be
    impossible is the primary phrase itself, which must appear exactly in the
    title, the H1 and a 140-155 character description.
    """
    p = primary.strip()
    if not p:
        return ["Primary keyword is empty."]
    problems = []
    for field, limit in (
        ("title", TITLE_MAX),
        ("H1", H1_MAX),
        ("description", DESCRIPTION_MAX - DESCRIPTION_WORDING_ROOM),
    ):
        if len(p) > limit:
            problems.append(
                f'Primary keyword "{p}" is {len(p)} characters; the {field} must contain it '
                f"exactly and allows at most {limit} for it."
            )
    return problems


def failed_checks(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the checks that blocked delivery, for alerts and feedback headers."""
    return [c for c in summary.get("checks", []) if not c.get("passed") and c.get("is_hard", True)]


def describe_check(check: dict[str, Any]) -> str:
    """One readable line per failed check: rule, requirement and measured value."""
    actual = str(check.get("actual", ""))
    measured = "" if actual in {"fail", "pass", ""} else f"; got {actual[:60]}"
    return f"{check.get('rule_key')} ({check.get('expected')}{measured})"


class DraftQualityError(ValueError):
    """The writer used every attempt; carries only what still blocked the last one.

    The November 11, 2026 alert printed the first 300 characters of the full
    scorecard, which were all passing checks, so the real failure was invisible.
    """

    def __init__(self, keyword: str, attempts: int, blockers: list[str]) -> None:
        self.topic_id = ""
        self.keyword = keyword
        self.attempts = attempts
        self.blockers = list(blockers) or ["No attempt produced a checkable draft"]
        super().__init__(
            f'"{keyword}" failed quality review after {attempts} attempts. Still failing: '
            + "; ".join(self.blockers)
        )


def structure_feedback(
    summary: dict[str, Any], metadata: dict[str, Any], primary: str, secondary: str
) -> str:
    """Give exact current values and measured limits without relaxing any checks."""
    details = {}
    for name, minimum, maximum in (
        ("title", 1, TITLE_MAX),
        ("description", DESCRIPTION_MIN, DESCRIPTION_MAX),
    ):
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
    exempt = exempt_secondary_fields(primary, secondary)
    if exempt:
        required = [f for f in SECONDARY_FIELD_LIMITS if f not in exempt] + [
            "body",
            "keyword metadata",
        ]
        hint = (
            "\nThese exact keyword phrases do not fit together in the "
            + ", ".join(exempt)
            + ". Use only the primary phrase there. The secondary is still required in: "
            + ", ".join(required)
            + ". Do not join them into an ungrammatical overlap."
        )
    return (
        "Fix every check marked failed without breaking any check marked passed.\n"
        + "Failed now: "
        + ", ".join(str(c.get("rule_key")) for c in failed_checks(summary))
        + "\n"
        + json.dumps(summary["checks"])
        + "\nMeasured metadata (counts exclude YAML quoting): "
        + json.dumps(details)
        + hint
    )
