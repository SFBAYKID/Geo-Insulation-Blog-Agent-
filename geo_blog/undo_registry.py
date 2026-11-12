"""Verify an article-only registry insertion before authorizing its inverse."""

from __future__ import annotations

import json


def verify_article_addition(before: str, after: str, slug: str) -> None:
    """Prove the merge added exactly one exported JSON entry and changed no other text."""
    anchor = "export const posts = ["
    if before.count(anchor) != 1 or after.count(anchor) != 1:
        raise ValueError("Ambiguous article registry")
    prefix, tail = after.split(anchor, 1)
    content = tail.lstrip()
    post, end = json.JSONDecoder().raw_decode(content)
    if (
        not isinstance(post, dict)
        or post.get("slug") != slug
        or post.get("agentManaged") is not True
    ):
        raise ValueError("Publication added a different or unmanaged article")
    remainder = content[end:]
    if not remainder.startswith(",") or prefix + anchor + remainder[1:] != before:
        raise ValueError("Publication modified more than the one article entry")
