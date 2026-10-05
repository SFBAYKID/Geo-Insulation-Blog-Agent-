"""Keyword measurements for Geo Insulation content reports."""

from __future__ import annotations

import re


def count_keyword_in_text(text: str, keyword: str) -> int:
    """Count case-insensitive occurrences of a keyword (or keyword phrase) in text.

    Why this lives here: every claim the bot makes about "your keyword
    appears N times" must trace to an exact count from a saved piece of
    text. Hand-rolling that count in workflows or recall handlers
    invites drift — one place to do the count, one place to test.

    Matching rules:
      - Case-insensitive (SEO conventions ignore case).
      - Word-boundary aware: "AI workflow" matches "AI Workflow"
        and "AI workflow." but NOT "workflowing" or
        "myAIworkflow".
      - Multi-word phrases match contiguous occurrences only — internal
        whitespace in the haystack can be any run of whitespace
        (spaces, tabs, newlines) so a phrase split across a line break
        still counts.
      - Empty text or empty keyword → 0.
    """
    if not text or not keyword:
        return 0
    # Build a regex from the keyword: each word in the keyword becomes
    # an escaped literal; the boundaries between words tolerate any
    # whitespace run. Word boundaries (\b) at the outer edges keep us
    # from matching INSIDE a longer word.
    words = [re.escape(w) for w in keyword.strip().split() if w.strip()]
    if not words:
        return 0
    pattern = r"\b" + r"\s+".join(words) + r"\b"
    return len(re.findall(pattern, text, flags=re.IGNORECASE))
