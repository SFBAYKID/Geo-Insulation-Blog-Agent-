"""Generate a clearly labeled illustration when no approved project photo matches.

Owner rule (September 28, 2026): a blog never goes out without an image. Real,
privacy-cleared project photos always come first (``media_catalog.attach_media``). Only
when none matches does this module ask Claude for an image brief, grounded in the
article and in factual descriptions of related real project photos, then generate the
image with OpenAI. The caption always begins "Illustration:" so the picture is never
presented as a customer installation.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .images import generate, validate_brief
from .settings import Settings

BRIEF_SYSTEM = """You write one image brief for an home insulation blog's hero image.
Return only JSON: {"scene": "...", "alt": "...", "caption": "..."}.
- scene (under 1500 characters): a realistic, well-lit photographic scene that shows the
  article's subject accurately, e.g. the insulation placement in a clean insulation work area.
  Use the reference descriptions of real project photos for realistic details. No people's
  faces, addresses, customer paperwork, logos, brand badges, text or watermarks.
- alt (under 300 characters): a factual description of what the image shows.
- caption (under 180 characters): must begin with "Illustration: " and describe the
  pictured scene without claiming it is a real customer's home or installation."""


def reference_descriptions(settings: Settings, keyword: str, limit: int = 5) -> list[str]:
    """Factual descriptions of related library photos, used as realism references."""
    path = settings.storage_dir / "drive-catalog" / "catalog.json"
    if not path.exists():
        return []
    images = json.loads(path.read_text()).get("images", [])
    words = {w for w in re.findall(r"[a-z]{4,}", keyword.casefold())}
    scored = []
    for image in images:
        text = str((image.get("analysis") or {}).get("factual_description", ""))
        score = sum(w in text.casefold() for w in words)
        if score:
            scored.append((score, text))
    return [text for _, text in sorted(scored, key=lambda s: -s[0])[:limit]]


def image_brief(writer: Any, draft: dict[str, Any], references: list[str]) -> dict[str, str]:
    """Ask the writer model for a bounded brief; enforce the illustration label."""
    from .content import response_json

    request = json.dumps(
        {
            "article_title": draft["front_matter"]["title"],
            "primary_keyword": draft["topic"]["keyword"],
            "summary": draft["front_matter"]["description"],
            "reference_descriptions_of_real_project_photos": references,
        }
    )
    response = writer.call(BRIEF_SYSTEM, request, max_tokens=1200, usage_stage="image_brief")
    brief = response_json(response)
    brief = {k: str(brief.get(k, "")).strip() for k in ("scene", "alt", "caption")}
    if not brief["caption"].startswith("Illustration: "):
        brief["caption"] = "Illustration: " + brief["caption"]
    validate_brief(brief)
    return brief


def ensure_image(settings: Settings, writer: Any, draft: dict[str, Any]) -> dict[str, Any]:
    """Return the draft unchanged when a real photo is attached; otherwise generate one."""
    if draft.get("media_status") == "ready" and draft.get("media_provenance"):
        return draft
    settings.require("image_generation_enabled", "openai_api_key")
    slug = draft["front_matter"]["slug"]
    reason = (
        f"No privacy-cleared, unused project photo matched '{draft['topic']['keyword']}' "
        f"(media status: {draft.get('media_status')})."
    )
    brief = image_brief(writer, draft, reference_descriptions(settings, draft["topic"]["keyword"]))
    authorized = settings.model_copy(
        update={"image_generation_enabled": True, "image_generation_fallback_reason": reason}
    )
    folder = settings.storage_dir / "generated-images" / slug
    assets = Path("assets/blog")
    hero = generate(authorized, brief, folder, assets=assets)
    derivative = assets / Path(hero["src"]).name
    data = derivative.read_bytes()
    provenance = {
        "id": "generated-" + slug,
        "drive_file_id": "generated:" + slug,
        "derivative_path": str(derivative),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": hero["width"],
        "height": hero["height"],
        "factual_description": brief["alt"],
        "caption": brief["caption"],
        "keyword_record_ids": [draft["topic"]["id"]],
        "relevance_tags": [],
        "publication_permission": "approved",
        "privacy_review": "cleared",
        "metadata_stripped": True,
        "origin": "generated",
        "fallback_reason": reason,
        "model": settings.image_model,
    }
    result = dict(draft, topic=dict(draft["topic"], hero=hero))
    result.update(media_status="ready", media_provenance=provenance, media_gallery=[])
    return result
