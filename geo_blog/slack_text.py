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


# A line that ends a sentence, and lines that keep tight spacing (quotes, list items).
_SENTENCE_END = re.compile(r"[.!?][)\"'’”]*$")
_TIGHT = re.compile(r"^\s*(?:>|[-•]\s|\*\s|\d+[.)]\s)")


def sentence_lines(text: str) -> str:
    """Put each sentence of a Slack message on its own line, with a blank line between.

    Reviewers asked for one sentence per line on October 1, 2026, and Chase asked
    on October 7 for a blank line after each sentence. Code, links, mentions and
    formatted spans stay intact; quotes and list items keep single line breaks.
    """
    parts = _CODE_BLOCK.split(text)
    # Odd indexes are fenced code blocks, which pass through untouched.
    return "".join(
        part if index % 2 else _space_sentences(_format_lines(part))
        for index, part in enumerate(parts)
    )


def _space_sentences(text: str) -> str:
    """Leave one blank line after each sentence-ending line before more prose."""
    lines = text.split("\n")
    out: list[str] = []
    for index, line in enumerate(lines):
        out.append(line)
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if (
            following.strip()
            and _SENTENCE_END.search(line.rstrip())
            and not _TIGHT.match(line)
            and not _TIGHT.match(following)
        ):
            out.append("")
    return "\n".join(out)


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
