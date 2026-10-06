"""Export approved articles into Geo's existing Astro posts.js data contract."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .content import parse_draft
from .images import webp_size
from .public_copy import internal_copy_findings
from .site_export import blocks
from .site_faq import extract_faq


def export_astro_post(draft: dict[str, Any], checkout: Path, day: str) -> list[str]:
    """Validate everything before writing the registry and immutable image."""
    from .local_preview import body_html

    published = date.fromisoformat(day)
    fm, markdown = parse_draft(draft["markdown"])
    slug = fm["slug"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Invalid slug")
    if internal_copy_findings(draft["markdown"]):
        raise ValueError("Internal workflow wording cannot be exported")
    body, faqs = extract_faq(markdown.replace("—", " - "))
    h1, content = blocks(body)
    if len(h1) > 70 or len(fm["title"]) > 55 or not 140 <= len(fm["description"]) <= 155:
        raise ValueError("Article metadata does not satisfy website limits")
    registry = checkout / "src/data/posts.js"
    original = registry.read_text()
    anchor = "export const posts = ["
    if original.count(anchor) != 1:
        raise ValueError("Geo article registry anchor missing or ambiguous")
    if re.search(r'["\']?slug["\']?\s*:\s*["\']' + re.escape(slug) + r'["\']', original):
        raise ValueError("Export must not overwrite an existing article")
    if draft.get("media_status") != "ready":
        raise ValueError("A finished image is required")
    provenance = draft["media_provenance"]
    source = Path(provenance["derivative_path"]).resolve()
    if not source.is_relative_to(Path("assets").resolve()) or source.suffix != ".webp":
        raise ValueError("Export image must be a WebP inside agent assets")
    data = source.read_bytes()
    hero = draft["topic"]["hero"]
    if hashlib.sha256(data).hexdigest() != provenance["sha256"]:
        raise ValueError("Image changed before export")
    if webp_size(data) != (hero["width"], hero["height"]) or len(data) > 1_000_000:
        raise ValueError("Image dimensions or weight do not match")
    caption = hero.get("caption") or provenance.get("caption", "")
    if provenance.get("origin") == "generated" and not caption.startswith("Illustration: "):
        raise ValueError("Generated image requires an Illustration caption")
    if not hero.get("alt") or internal_copy_findings(hero["alt"]):
        raise ValueError("Image requires public descriptive alt text")
    image = f"/blog/media/{slug}-hero.webp"
    if hero["src"] != image:
        raise ValueError("Hero must use the canonical article image path")
    target = checkout / "public" / image.lstrip("/")
    if target.exists():
        raise ValueError("Export must not overwrite an existing image")
    post = {
        "slug": slug,
        "title": fm["title"],
        "headline": h1,
        "description": fm["description"],
        "excerpt": fm["description"],
        "date": day,
        "modified": day,
        "dateLabel": published.strftime("%B %d, %Y").replace(" 0", " "),
        "category": "Home Insulation",
        "categorySlug": "home-insulation",
        "categories": ["Home Insulation"],
        "image": image,
        "imageAlt": hero["alt"],
        "imageCaption": caption,
        "readingMinutes": max(1, len(markdown.split()) // 200),
        "sourceUrl": f"https://geo-insulation.com/blog/{slug}/",
        "body": body_html(content),
        "faqs": faqs,
        "agentManaged": True,
    }
    encoded = json.dumps(post, ensure_ascii=False, indent=2)
    updated = original.replace(anchor, anchor + "\n" + encoded + ",", 1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    registry.write_text(updated)
    return ["src/data/posts.js", str(target.relative_to(checkout))]
