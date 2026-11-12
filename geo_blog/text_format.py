"""Keyword measurements for Geo Insulation content reports."""

from __future__ import annotations

import re

# Whitespace or hyphen variants (ASCII, Unicode hyphen, non-breaking hyphen) between words.
_SEPARATORS = r"[\s\-‐‑]+"
# Markdown link/image targets "](url)" or "](url "title")", angle-bracket autolinks, bare URLs.
_LINK_TARGET = re.compile(r"\]\(\s*<?[^)\s>]*>?(?:\s+\"[^\"]*\")?\s*\)")
_BARE_URL = re.compile(r"<?\b(?:https?://|www\.)[^\s<>()\]]+>?", re.IGNORECASE)


def same_phrase(left: str, right: str) -> bool:
    """Equal keyword phrases, ignoring case and hyphen-versus-space separators."""

    def words(value: str) -> list[str]:
        return [w for w in re.split(_SEPARATORS, value.strip().casefold()) if w]

    return bool(words(left)) and words(left) == words(right)


def strip_link_targets(text: str) -> str:
    """Keep link labels and prose; drop every link address."""
    return _BARE_URL.sub(" ", _LINK_TARGET.sub("] ", text))


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
      - A hyphen and a space between words are the same separator, so
        "blown in" matches "blown-in" and vice versa. Search engines treat
        them alike, and the November 11, 2026 topic paired "blown in" with
        "blown-in", which a writer cannot satisfy both ways at once.
      - Link addresses are not prose. Markdown link targets and bare URLs
        are removed first, so "/blog/wet-attic-insulation/" never counts as
        "wet attic insulation" (it would satisfy placement checks with no
        readable text and inflate the repetition count). Link labels count.
      - Empty text or empty keyword → 0.
    """
    if not text or not keyword:
        return 0
    text = strip_link_targets(text)
    # Build a regex from the keyword: each word in the keyword becomes
    # an escaped literal; the boundaries between words tolerate any
    # whitespace run or a single hyphen. Word boundaries (\b) at the outer
    # edges keep us from matching INSIDE a longer word.
    words = [re.escape(w) for w in re.split(_SEPARATORS, keyword.strip()) if w]
    if not words:
        return 0
    pattern = r"\b" + r"(?:\s+|[-‐‑])".join(words) + r"\b"
    return len(re.findall(pattern, text, flags=re.IGNORECASE))
