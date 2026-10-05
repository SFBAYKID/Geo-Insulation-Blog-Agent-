"""Apply exact, unambiguous text edits without regenerating untouched article text."""

from __future__ import annotations

from typing import Any


def apply_edits(original: Any, edits: Any) -> Any:
    """Apply unique nonoverlapping replacements against the original immutable text."""
    if not isinstance(edits, list) or len(edits) > 20:
        raise ValueError("Return at most 20 exact text replacements")
    spans = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"old", "new"}:
            raise ValueError("Each edit needs only old and new text")
        old, new = edit["old"], edit["new"]
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("old must be nonempty text; new must be text")
        # A replacement that changes nothing cannot help, but it can sit inside a real
        # edit and make the whole correction look like an overlap (September 21, 2026).
        if old == new:
            continue
        if original.count(old) != 1:
            raise ValueError(
                "Each old text must match exactly once in the original; include surrounding text"
            )
        start = original.index(old)
        spans.append((start, start + len(old), new))
    if not spans:
        raise ValueError(
            "No edit changed the article; return edits whose new text differs from the old"
        )
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError("Text replacements cannot overlap")
    text = original
    for start, end, new in reversed(spans):
        text = text[:start] + new + text[end:]
    return text
