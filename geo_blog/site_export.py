"""Parse checked Markdown and export into Geo's Astro article data contract.

Unsupported Markdown fails explicitly rather than silently dropping text.
Website writes require a finished image and refuse duplicate article slugs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def inline(text: str) -> list[Any]:
    """Preserve Markdown links and bold runs in the site's small inline vocabulary."""
    # The site inline model has no nested marks; preserve a bold link as a link.
    text = re.sub(r"\*\*(\[[^\]]+\]\((?:https?://|/)[^\s)]+\))\*\*", r"\1", text)
    result: list[Any] = []
    pattern = r"\[([^\]]+)\]\((https?://[^\s)]+|/[^\s)]*)\)|\*\*([^*]+)\*\*"
    end = 0
    for match in re.finditer(pattern, text):
        if match.start() > end:
            result.append(text[end : match.start()])
        if match[3]:
            result.append({"bold": match[3]})
        else:
            result.append({"text": match[1], "href": match[2]})
        end = match.end()
    if end < len(text):
        result.append(text[end:])
    return result


def blocks(body: str) -> tuple[str, list[dict[str, Any]]]:
    """Convert paragraphs, headings and flat lists; refuse unsupported syntax."""
    result: list[dict[str, Any]] = []
    title = ""
    for part in re.split(r"\n\s*\n", body.strip()):
        if part.startswith("# "):
            title = part[2:].strip()
        elif match := re.fullmatch(r"(#{2,3}) (.+)", part):
            result.append(
                {
                    "kind": "heading",
                    "level": len(match[1]),
                    "id": f"section-{len(result) + 1}",
                    "text": match[2],
                }
            )
        elif re.match(r"(?:- |\d+\. )", part):
            items = []
            for line in part.splitlines():
                item = re.fullmatch(r"(?:- |\d+\. )(.+)", line)
                if not item:
                    raise ValueError("Nested or wrapped lists require editorial conversion")
                text = item[1]
                lead = re.fullmatch(r"\*\*(.+?)\*\*\s*(.*)", text)
                if "[" in text or (not lead and "**" in text):
                    raise ValueError("Unsupported inline list formatting")
                items.append({"lead": lead[1], "text": lead[2]} if lead else {"text": text})
            result.append({"kind": "list", "ordered": part[0].isdigit(), "items": items})
        else:
            if re.search(r"(^[>#|`]|<[^>]+>)", part, re.M):
                raise ValueError("Unsupported Markdown block")
            result.append({"kind": "paragraph", "content": inline(" ".join(part.splitlines()))})
    if not title:
        raise ValueError("Missing article heading")
    return title, result


def export_post(draft: dict[str, Any], checkout: Path, day: str) -> list[str]:
    """Append checked Geo content without modifying existing articles or templates."""
    from .astro_export import export_astro_post

    return export_astro_post(draft, checkout, day)
