"""Provider-free, phrase-preserving repairs for overlong metadata."""

from __future__ import annotations

import re

from .text_format import count_keyword_in_text


def fit_length(value: str, phrases: list[str], minimum: int, maximum: int) -> str | None:
    """Trim whole words outside required phrases, preserving their original order.

    Return None for impossible phrases or short source copy; never invent claims or
    cut a keyword to make the counter pass. The normal editor still reviews repairs.
    """
    value = " ".join(value.split())
    if any(not count_keyword_in_text(value, phrase) for phrase in phrases):
        return None
    while len(value) > maximum:
        words = list(re.finditer(r"\S+", value))
        replacement = None
        for word in reversed(words):
            candidate = (value[: word.start()] + value[word.end() :]).strip()
            candidate = " ".join(candidate.split()).rstrip(" ,;:-")
            if len(candidate) >= minimum and all(
                count_keyword_in_text(candidate, phrase) for phrase in phrases
            ):
                replacement = candidate
                break
        if replacement is None:
            return None
        value = replacement
    return value if minimum <= len(value) <= maximum else None


def repair_heading(text: str, primary: str) -> str:
    """Shorten a single overlong H1 before validation and editorial review."""
    headings = list(re.finditer(r"^# (.+)$", text, re.M))
    if len(headings) != 1 or len(headings[0][1]) <= 70:
        return text
    heading = headings[0]
    phrases = [primary] if count_keyword_in_text(heading[1], primary) else []
    fixed = fit_length(heading[1], phrases, 1, 70)
    if fixed is None:
        fixed = primary.strip()
    return text[: heading.start(1)] + fixed + text[heading.end(1) :]


def description_fallback(phrases: list[str]) -> str:
    """Build a measured neutral summary when trimming cannot reach the window.

    These sentences make no business or technical claims. A repaired summary still
    goes through the normal factual/clarity editor before publication.
    """
    from itertools import combinations

    seed = "; ".join(dict.fromkeys(phrases)).rstrip(".;: ") + "."
    sentences = (
        "Read this homeowner guide.",
        "Explore the topic.",
        "Review the key points.",
        "Consider questions for an insulation professional.",
        "Find more context in the article.",
        "Read on.",
    )
    for size in range(1, len(sentences) + 1):
        for selected in combinations(sentences, size):
            candidate = seed + " " + " ".join(selected)
            if 140 <= len(candidate) <= 155:
                return candidate
    raise ValueError("Required metadata phrases cannot fit the description window")
