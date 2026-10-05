"""Split portable Markdown into bounded Slack messages without dropping text."""

from __future__ import annotations

import re


def article_chunks(article: str, limit: int = 3000) -> list[str]:
    """Prefer paragraph boundaries; split oversized paragraphs safely as text."""
    if limit < 100:
        raise ValueError("Chunk limit is too small")
    chunks: list[str] = []
    remaining = article.strip()
    while remaining:
        end = min(len(remaining), limit)
        if end < len(remaining):
            boundary = remaining.rfind("\n\n", 0, end)
            if boundary > limit // 2:
                end = boundary
        chunks.append(remaining[:end])
        remaining = remaining[end:].lstrip()
    return chunks


# Slack spans whose contents must never be broken: code blocks, inline code,
# links/mentions and single-line bold/italic/strike (a bold title such as
# "*Does PDR Work? Full Guide*" is one unit, not two sentences).
_CODE_BLOCK = re.compile(r"(```.*?```)", re.S)
_PROTECTED = re.compile(r"`[^`\n]*`|<[^>\n]*>|\*[^*\n]+\*|_[^_\n]+_|~[^~\n]+~")
# A sentence end, optional closing punctuation, then a space before what starts a
# new sentence: a capital, a mention/link, formatting, an opening quote or a digit.
_BOUNDARY = re.compile(r"(?<=[.!?])([)\"'’”]*)[ \t]+(?=[A-Z<*_\"“‘(\d])")
# Words whose trailing period is not a sentence end.
_ABBREVIATIONS = frozenset(
    {
        "e.g.",
        "i.e.",
        "etc.",
        "vs.",
        "mr.",
        "mrs.",
        "ms.",
        "dr.",
        "st.",
        "no.",
        "approx.",
        "u.s.",
        "inc.",
    }
)


def sentence_lines(text: str) -> str:
    """Put each sentence of a Slack message on its own line, like a text message.

    Reviewers asked for this house style on October 1, 2026: no paragraph
    blocks. Code, links, mentions and formatted spans stay intact, and a
    continuation inside a "> " quote keeps the quote marker.
    """
    parts = _CODE_BLOCK.split(text)
    # Odd indexes are fenced code blocks, which pass through untouched.
    return "".join(part if index % 2 else _format_lines(part) for index, part in enumerate(parts))


def _format_lines(text: str) -> str:
    """Apply sentence breaks line by line, never inside a protected inline span."""
    out: list[str] = []
    for line in text.split("\n"):
        quote_prefix = "> " if line.startswith(">") else ""
        spans = [match.span() for match in _PROTECTED.finditer(line)]

        def replace(
            match: re.Match[str],
            line: str = line,
            spans: list[tuple[int, int]] = spans,
            prefix: str = quote_prefix,
        ) -> str:
            if any(start < match.start() < end for start, end in spans):
                return match.group(0)
            before = line[: match.start()].split()
            word = before[-1].lower() if before else ""
            # Keep abbreviations and single initials ("A. Smith") on one line.
            if word in _ABBREVIATIONS or re.fullmatch(r"[a-z]\.", word):
                return match.group(0)
            return match.group(1) + "\n" + prefix

        out.append(_BOUNDARY.sub(replace, line))
    return "\n".join(out)
