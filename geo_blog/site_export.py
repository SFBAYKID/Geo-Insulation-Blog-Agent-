"""Translate checked Markdown into the real Geo site's typed article blocks.

Only an isolated preview branch is changed. Unsupported Markdown fails explicitly
rather than silently dropping text. Media must be approved or visibly pending.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from .content import parse_draft
from .public_copy import internal_copy_findings
from .site_faq import extract_faq


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
    """Write a post, registry and test entries; never overwrite an existing slug."""
    date.fromisoformat(day)
    fm, body = parse_draft(draft["markdown"])
    if internal_copy_findings(draft["markdown"]):
        raise ValueError("Internal workflow wording cannot be exported")
    slug = fm["slug"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Invalid slug")
    web = checkout / "geo-web"
    dest = web / f"src/content/blog/{slug}.ts"
    if dest.exists():
        raise ValueError("Preview export must not overwrite an existing article")
    body, faq = extract_faq(body.replace("—", " - "))
    h1, content = blocks(body)
    if len(h1) > 70 or len(fm["title"]) > 55 or not 140 <= len(fm["description"]) <= 155:
        raise ValueError("Article metadata does not satisfy website limits")
    hero = draft["topic"].get("hero")
    if hero:
        hero = {k: v for k, v in hero.items() if k in {"src", "alt", "width", "height"}}
    if hero and internal_copy_findings(hero.get("alt", "")):
        raise ValueError("Image alt text contains internal workflow wording")
    if draft.get("media_status") == "ready":
        from .media_catalog import attach_media
        from .settings import Settings

        provenance = draft["media_provenance"]
        if provenance.get("origin") == "generated":
            # Generated fallbacks are not catalog photos; verify the exact file instead.
            data = Path(provenance["derivative_path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != provenance["sha256"]:
                raise ValueError("Generated image changed before export")
        else:
            checked = attach_media(Settings(), draft)
            if checked.get("media_provenance") != provenance:
                raise ValueError("Photo provenance changed before export")
        target = web / "public" / hero["src"].lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(draft["media_provenance"]["derivative_path"], target)
    else:
        target = web / f"public/blog/{slug}-pending.svg"
        target.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="500" viewBox="0 0 1200 500"><rect width="1200" height="500" fill="#171717"/><text x="600" y="220" text-anchor="middle" fill="white" font-family="sans-serif" font-size="48">DRAFT PREVIEW</text><text x="600" y="290" text-anchor="middle" fill="#cccccc" font-family="sans-serif" font-size="30">Approved project photo pending</text></svg>'
        )
        hero = {
            "src": f"/blog/{target.name}",
            "alt": "Draft preview placeholder: approved project photo pending",
            "width": 1200,
            "height": 500,
        }
    post = dict(
        slug=slug,
        h1=h1,
        metaTitle=fm["title"],
        metaDescription=fm["description"],
        keywords=fm["keywords"],
        headlineKeywords=[
            value
            for value in [draft["topic"]["keyword"], draft["topic"].get("secondary_keyword")]
            if value
        ],
        excerpt=fm["description"],
        category=draft["topic"].get("product", {}).get("name", "Vehicle Care"),
        datePublished=day,
        dateModified=day,
        hero=hero,
        schemaDescription=fm["description"],
        body=content,
        faqs=faq,
    )
    symbol = slug.replace("-", "_").upper()
    dest.write_text(
        '/** Blog-agent draft for human review. Do not merge until copy and media are approved. */\nimport type { Post } from "./types";\n\nexport const '
        + symbol
        + ": Post = "
        + json.dumps(post, ensure_ascii=False, indent=2)
        + ";\n"
    )
    for filename, prefix in [("index.ts", "@/content/blog/"), ("posts.test.ts", "./")]:
        path = web / "src/content/blog" / filename
        text = path.read_text()
        suffix = ".ts" if filename.endswith("test.ts") else ""
        match = re.search(r"^import ", text, re.M)
        if match is None:
            raise ValueError("Website registry import anchor missing")
        pos = match.start()
        text = text[:pos] + f'import {{ {symbol} }} from "{prefix}{slug}{suffix}";\n' + text[pos:]
        text = text.replace(
            "const POSTS: readonly Post[] = [",
            "const POSTS: readonly Post[] = [\n  " + symbol + ",",
        )
        path.write_text(text)
    check = web / "scripts/check-rendered.mjs"
    check.write_text(
        check.read_text().replace(
            "const ROUTES = [",
            "const ROUTES = [\n  "
            + json.dumps({"path": "/blog/" + slug, "needle": h1, "title": fm["title"]})
            + ",",
        )
    )
    return [
        str(p.relative_to(checkout))
        for p in [
            dest,
            target,
            web / "src/content/blog/index.ts",
            web / "src/content/blog/posts.test.ts",
            check,
        ]
    ]
