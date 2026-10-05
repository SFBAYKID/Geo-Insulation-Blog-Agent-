"""Export a review draft to the Geo Insulation website's preview format."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import Store

import json
import re
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from .content import parse_draft

HUBS = {
    "attic-insulation",
    "installation-process",
    "air-sealing",
    "energy-efficiency",
    "home-comfort",
}


def article_location(draft: dict[str, Any]) -> Any:
    """Validate the draft route and derive safe content and route paths."""
    fm, _ = parse_draft(draft["markdown"])
    hub = draft["topic"].get("hub", "attic-insulation")
    if hub not in HUBS:
        raise ValueError("Unknown Geo Insulation topic hub")
    slug = fm["slug"]
    flat = False
    planned = draft["topic"].get("planned_url")
    if planned:
        from urllib.parse import urlparse

        parsed = urlparse(planned)
        if parsed.netloc and (parsed.scheme != "https" or parsed.hostname != "geo-insulation.com"):
            raise ValueError("Planned URL must belong to Geo Insulation")
        match = re.fullmatch(r"/blog/([a-z0-9-]+)/([a-z0-9-]+)/?", parsed.path)
        flat_match = re.fullmatch(r"/blog/([a-z0-9-]+)/?", parsed.path)
        if parsed.query or parsed.fragment:
            raise ValueError("Planned URL must match the assigned blog hub")
        if flat_match and flat_match[1] not in HUBS | {"media", "page"}:
            slug = flat_match[1]
            flat = True
        elif match and match[1] == hub:
            slug = match[2]
        else:
            raise ValueError("Planned URL must match the assigned blog hub")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Unsafe article slug")
    return {
        "hub": hub,
        "slug": slug,
        "path": f"/blog/{slug}/" if flat else f"/blog/{hub}/{slug}/",
        "mdx_path": f"src/content/blog/{hub}/{slug}.mdx",
        "route_path": f"src/app/blog/{slug}/page.tsx" if flat else None,
    }


def flat_route(hub: str, slug: str) -> str:
    """The existing website's static route pattern; no model-generated code."""
    if (
        hub not in HUBS
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
        or slug in HUBS | {"media", "page"}
    ):
        raise ValueError("Unsafe flat article route")
    return (
        'import { notFound } from "next/navigation";\n'
        'import { BlogPost, postMetadata } from "@/components/blog/BlogPost";\n'
        'import { getPost } from "@/lib/blog";\n\n'
        f'const getArticle = () => getPost("{hub}", "{slug}");\n\n'
        "export async function generateMetadata() {\n"
        "  const post = await getArticle();\n"
        "  return post ? postMetadata(post) : {};\n}\n\n"
        "export default async function ArticlePage() {\n"
        "  const post = await getArticle();\n"
        "  if (!post) notFound();\n"
        "  return <BlogPost post={post} />;\n}\n"
    )


def flat_routes(hub: str, slug: str) -> Any:
    """Build deterministic route files for a validated flat article URL."""
    page = flat_route(hub, slug)
    image = (
        'import { ImageResponse } from "next/og";\n'
        'import { notFound } from "next/navigation";\n'
        'import { BlogPostImage } from "@/components/blog/BlogPostImage";\n'
        'import { getPost } from "@/lib/blog";\n\n'
        'export const alt = "Geo Insulation blog post";\n'
        "export const size = { width: 1200, height: 630 };\n"
        'export const contentType = "image/png";\n\n'
        "export default async function Image() {\n"
        f'  const post = await getPost("{hub}", "{slug}");\n'
        "  if (!post) notFound();\n"
        "  return new ImageResponse(<BlogPostImage title={post.title} />, size);\n}\n"
    )
    return {
        f"src/app/blog/{slug}/page.tsx": page,
        f"src/app/blog/{slug}/opengraph-image.tsx": image,
    }


def export_preview(store: Store, draft_id: str, checkout: Path) -> Any:
    """Export an unpublished article into an isolated checkout without overwriting content."""
    row = store.get(draft_id)
    if not row or row["status"] != "ready":
        raise ValueError("Only a ready draft can be exported")
    draft = json.loads(row["payload"])
    fm, body = parse_draft(draft["markdown"])
    location = article_location(draft)
    root = Path(checkout)
    target = root / location["mdx_path"]
    if target.exists():
        raise ValueError("Preview export would overwrite an existing article")
    if location["route_path"] and (root / location["route_path"]).parent.exists():
        raise ValueError("Flat article URL already has a website route")
    body = re.sub(r"^# .+\n+", "", body, count=1)
    data = {
        "title": fm.get("display_title", fm["title"]),
        "seoTitle": fm["title"],
        "description": fm["description"],
        "date": datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat(),
        "primaryKeyword": fm["keywords"][0],
        "supportingKeywords": fm["keywords"][1:],
        "draft": True,
    }
    if fm.get("standfirst"):
        data["standfirst"] = fm["standfirst"]
    if location["route_path"]:
        data["path"] = location["path"]
    if draft["topic"].get("exercise"):
        exercise = draft["topic"]["exercise"]
        from .visuals import validate_exercise

        validate_exercise(exercise)
        if "## " + exercise["section"] + "\n" not in body:
            raise ValueError("The reader exercise needs its matching Markdown section")
        data["exercise"] = exercise
    if draft["topic"].get("review_lab"):
        lab = draft["topic"]["review_lab"]
        if "## " + lab["section"] + "\n" not in body:
            raise ValueError("The visual exercise needs its matching Markdown section")
        data["reviewLab"] = lab
    if draft["topic"].get("hero"):
        hero = draft["topic"]["hero"]
        if (
            not re.fullmatch(r"/blog/media/[a-z0-9-]+\.(?:png|webp|jpg)", hero["src"])
            or not hero.get("alt", "").strip()
        ):
            raise ValueError("Hero needs a safe media path and descriptive alt text")
        data["hero"] = hero
        source = Path("assets") / Path(hero["src"]).name
        dest = root / "public" / hero["src"].lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    if draft["topic"].get("diagram"):
        d = draft["topic"]["diagram"]
        if (
            not re.fullmatch(r"/blog/media/[a-z0-9-]+\.(?:svg|png|webp)", d["src"])
            or not d.get("alt", "").strip()
        ):
            raise ValueError("Diagram needs a safe media path and descriptive alt text")
        data["diagram"] = d
        source = Path("assets") / Path(d["src"]).name
        dest = root / "public" / d["src"].lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True) + "---\n\n" + body
    )
    if location["route_path"]:
        for name, content in flat_routes(location["hub"], location["slug"]).items():
            route = root / name
            route.parent.mkdir(parents=True, exist_ok=True)
            route.write_text(content)
    return location["path"]
