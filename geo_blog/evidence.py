"""Bounded, verbatim evidence packets; full fetched/provider evidence stays on disk."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from .company import SERVICE_PATHS


def excerpt(text: str, limit: int) -> str:
    """Bound evidence at a natural sentence boundary without joining unrelated claims."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    # Preserve complete sentences where possible; never join unrelated fragments into a claim.
    end = max(
        text.rfind(". ", 0, limit),
        text.rfind("? ", 0, limit),
        text.rfind("! ", 0, limit),
    )
    return (
        text[: end + 1 if end > limit // 2 else text.rfind(" ", 0, limit)]
        + " [Excerpt ends; omitted content is not evidence.]"
    )


def catalog_packet(company: list[dict[str, Any]]) -> Any:
    """Bound verified service-page evidence for service selection."""
    return [
        dict(url=p["url"], text=excerpt(p["text"], 1800))
        for p in company
        if urlparse(p["url"]).path in SERVICE_PATHS
    ]


def selected_packet(company: list[dict[str, Any]], topic: dict[str, Any]) -> Any:
    """Keep full saved selected-product text and short identity/internal-link excerpts."""
    selected = topic.get("product", {}).get("url")
    result = []
    for page in company:
        url = page["url"]
        if url == selected:
            result.append(dict(url=url, text=page["text"]))
        elif "/blog/" in url:
            result.append(dict(url=url, text=excerpt(page["text"], 900)))
        elif url.rstrip("/") == "https://geo-insulation.com":
            result.append(dict(url=url, text=excerpt(page["text"], 1200)))
    return result


def source_context(
    topic: dict[str, Any], company: list[dict[str, Any]], research: str, urls: set[str]
) -> str:
    """Separate article instructions from untrusted evidence and allowed citation URLs."""
    # Operational estimates and visual assets are not research evidence.
    brief = {
        k: v
        for k, v in topic.items()
        if k not in {"model_cost_estimate", "hero", "exercise", "diagram", "review_lab"}
    }
    return json.dumps(
        {
            "brief": brief,
            "company_pages": selected_packet(company, topic),
            "research": research,
            "allowed_urls": sorted(urls),
        },
        sort_keys=True,
    )
