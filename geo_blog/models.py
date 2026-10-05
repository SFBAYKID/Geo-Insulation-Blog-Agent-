"""Structural quality results for Geo Insulation articles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StructureCheck:
    """One row in the structure report.

    `expected` is plain text describing the documented target. `actual`
    is what we measured. `passed` is the binary verdict. `is_hard` flags
    whether a failure here should block the commit.
    """

    rule_key: str
    expected: str
    actual: str
    passed: bool
    is_hard: bool


@dataclass(frozen=True)
class StructureReport:
    """The full check report for one draft.

    `passed` rolls up the AND of every hard check. Soft checks contribute
    to `notes` (printed in the local CLI report and Slack reply) but don't
    block a commit.
    """

    checks: tuple[StructureCheck, ...]
    word_count: int

    @property
    def passed(self) -> bool:
        """True iff every hard check passes."""
        return all(c.passed for c in self.checks if c.is_hard)

    @property
    def hard_failures(self) -> tuple[StructureCheck, ...]:
        return tuple(c for c in self.checks if c.is_hard and not c.passed)

    @property
    def summary(self) -> dict[str, Any]:
        """Compact dict for logging / Slack rendering."""
        return {
            "passed": self.passed,
            "word_count": self.word_count,
            "checks": [
                {
                    "rule_key": c.rule_key,
                    "expected": c.expected,
                    "actual": c.actual,
                    "passed": c.passed,
                    "is_hard": c.is_hard,
                }
                for c in self.checks
            ],
        }
