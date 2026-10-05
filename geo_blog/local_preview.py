"""Export a checked draft as a self-contained, unpublished Geo design preview."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .content import validate
from .images import webp_size
from .model_usage import fingerprint
from .site_export import blocks
from .site_faq import extract_faq

ASSETS = Path(__file__).parent / "preview_assets"
SITE = "https://geo-insulation.com/"
REFERENCE = SITE + "blog/signs-of-poor-insulation-san-antonio-homes/"


def escape(value: Any) -> str:
    """Escape all authored content at the HTML boundary."""
    return html.escape(str(value), quote=True)


def inline_html(parts: list[Any]) -> str:
    """Render the checked inline vocabulary without executing authored markup."""
    result = []
    for part in parts:
        if isinstance(part, str):
            result.append(escape(part))
        elif "bold" in part:
            result.append("<strong>" + escape(part["bold"]) + "</strong>")
        else:
            url = urljoin(SITE, part["href"])
            if urlparse(url).scheme not in {"https", "http"} or not urlparse(url).hostname:
                raise ValueError("Unsafe article link")
            external = urlparse(url).hostname != "geo-insulation.com"
            attributes = ' target="_blank" rel="noopener noreferrer"' if external else ""
            result.append(f'<a href="{escape(url)}"{attributes}>{escape(part["text"])}</a>')
    return "".join(result)


def body_html(content: list[dict[str, Any]]) -> str:
    """Render paragraphs, headings and flat lists with stable navigation IDs."""
    out = []
    for block in content:
        if block["kind"] == "paragraph":
            out.append("<p>" + inline_html(block["content"]) + "</p>")
        elif block["kind"] == "heading":
            level = block["level"]
            if level not in {2, 3}:
                raise ValueError("Unsupported heading")
            out.append(f'<h{level} id="{escape(block["id"])}">{escape(block["text"])}</h{level}>')
        elif block["kind"] == "list":
            tag = "ol" if block["ordered"] else "ul"
            items = []
            for item in block["items"]:
                lead = "<strong>" + escape(item["lead"]) + "</strong> " if item.get("lead") else ""
                items.append("<li>" + lead + escape(item["text"]) + "</li>")
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
        else:
            raise ValueError("Unsupported article block")
    return "\n".join(out)


def header(asset_prefix: str, library_url: str) -> str:
    """Match Geo navigation with a separate, unambiguous local-review notice."""
    links = [
        ("Home", ""),
        ("About", "about-us/"),
        ("Services", "expert-insulation-services/"),
        ("Blog", "blog/"),
        ("Reviews", "reviews/"),
        ("Contact", "contact-us/"),
    ]
    nav = "".join(f'<a href="{SITE + path}">{label}</a>' for label, path in links)
    return f'''<a class="skip" href="#main">Skip to content</a>
<div class="review-bar"><div class="wrap"><strong>LOCAL DRAFT · NOT PUBLISHED</strong><div class="review-links"><a href="{library_url}">Draft library</a><a href="{REFERENCE}" target="_blank" rel="noopener">Compare live design ↗</a></div></div></div>
<header class="site-header"><div class="contact-bar"><div class="wrap"><a href="tel:2108485658">Call: 210-848-5658</a><a href="mailto:service@geo-insulation.com">service@geo-insulation.com</a></div></div>
<div class="wrap nav-row"><a href="{SITE}" aria-label="Geo-Insulation home"><img class="logo" src="{asset_prefix}geo-logo.webp" alt="Geo-Insulation, LLC logo" width="170" height="93"></a>
<nav class="main-nav" aria-label="Primary">{nav}</nav><a class="button nav-call" href="tel:2108485658">Call 210-848-5658</a>
<details class="mobile-nav"><summary>Menu ☰</summary><nav aria-label="Mobile">{nav}</nav></details></div></header>'''


def footer(asset_prefix: str) -> str:
    """Use verified branding and contact links without copying old article claims."""
    return f'''<section class="cta"><div class="wrap"><h2>Let Us Take Care of Your Insulation Project</h2><p>Talk with Geo-Insulation about your San Antonio home.</p><div class="buttons"><a class="button light" href="{SITE}expert-insulation-services/">View services</a><a class="button light" href="{SITE}contact-us/">Contact us</a></div></div></section>
<footer class="site-footer"><div class="wrap"><div class="footer-row"><div><img class="logo" src="{asset_prefix}geo-logo.webp" alt="Geo-Insulation, LLC" width="170" height="93"><p>Home insulation and radiant barrier services in San Antonio.</p></div><div><h2>Explore</h2><a href="{SITE}about-us/">About Geo-Insulation</a><a href="{SITE}expert-insulation-services/">Our services</a><a href="{SITE}blog/">Published articles</a></div><div><h2>Get In Touch</h2><a href="tel:2108485658">210-848-5658</a><a href="mailto:service@geo-insulation.com">service@geo-insulation.com</a><a href="{SITE}contact-us/">Contact Geo-Insulation</a></div></div><div class="copyright">Private local preview for design and editorial review. Nothing on this page has been published.</div></div></footer>'''


def document(title: str, content: str, assets: str, description: str = "") -> str:
    """Create a noindex HTML file with local assets and no tracking or forms."""
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><meta name="description" content="{escape(description)}"><title>{escape(title)} | Geo local preview</title><link rel="stylesheet" href="{assets}blog.css"></head><body>{content}</body></html>'''


def export_preview(payload: Path, destination: Path = Path("previews")) -> Path:
    """Verify approved prose/image integrity, then export without remote side effects."""
    draft = json.loads(payload.read_text())
    approved = json.loads((payload.parent / "approved-prose.json").read_text())
    if approved.get("draft_sha256") != fingerprint(approved["draft"]):
        raise ValueError("Approved prose checksum mismatch")
    if approved["draft"]["markdown"] != draft["markdown"]:
        raise ValueError("Draft changed after editorial approval")
    topic = draft["topic"]
    fm, body, report = validate(
        draft["markdown"],
        topic["keyword"],
        set(draft["sources"]),
        topic.get("secondary_keyword", ""),
    )
    if not report.passed or draft.get("media_status") != "ready":
        raise ValueError("A checked article and finished image are required")
    slug = fm["slug"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Invalid local article slug")
    source = Path(draft["media_provenance"]["derivative_path"]).resolve()
    if not source.is_relative_to(Path("assets").resolve()) or source.suffix != ".webp":
        raise ValueError("Preview media must be a WebP inside the agent assets folder")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != draft["media_provenance"]["sha256"]:
        raise ValueError("Image changed after generation or approval")
    hero = topic["hero"]
    if webp_size(data) != (hero["width"], hero["height"]) or len(data) > 1_000_000:
        raise ValueError("Preview image dimensions or page weight do not match")
    caption = hero.get("caption") or draft["media_provenance"].get("caption", "")
    if draft["media_provenance"].get("origin") == "generated" and not caption.startswith(
        "Illustration: "
    ):
        raise ValueError("Generated images require the Illustration caption")
    body, faqs = extract_faq(body)
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f["question"],
                "acceptedAnswer": {"@type": "Answer", "text": f["answer"]},
            }
            for f in faqs
        ],
    }
    schema_html = (
        '<script type="application/ld+json">'
        + json.dumps(schema).replace("<", "\\u003c")
        + "</script>"
    )
    h1, content = blocks(body)
    dest = destination / slug
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ASSETS, destination / "assets", dirs_exist_ok=True)
    (dest / "hero.webp").write_bytes(data)
    (dest / "article.md").write_text(draft["markdown"])
    portable = {
        "status": "local_draft",
        "publication_approved": False,
        "title": h1,
        "metadata": fm,
        "body": content,
        "faqs": faqs,
        "hero": {**hero, "src": "hero.webp", "caption": caption},
        "sources": draft["sources"],
        "markdown_sha256": fingerprint(draft["markdown"]),
    }
    (dest / "article.json").write_text(json.dumps(portable, ensure_ascii=False, indent=2))
    faq_html = "".join(
        f"<details><summary>{escape(f['question'])}</summary><p>{escape(f['answer'])}</p></details>"
        for f in faqs
    )
    toc = "".join(
        f'<a href="#{escape(b["id"])}">{escape(b["text"])}</a>'
        for b in content
        if b["kind"] == "heading" and b["level"] == 2
    )
    reading = max(1, round(report.word_count / 220))
    article = f'''<main id="main"><section class="hero"><img class="hero-image" src="hero.webp" alt="{escape(hero["alt"])}" width="{int(hero["width"])}" height="{int(hero["height"])}"><div class="wrap"><nav class="crumbs" aria-label="Breadcrumb"><a href="{SITE}">Home</a><span>/</span><a href="../index.html">Blog</a></nav><div class="meta"><span class="pill">Home insulation</span><span>Draft for review</span><span>· {reading} min read</span></div><h1>{escape(h1)}</h1></div></section>
<div class="caption"><div class="wrap">{escape(caption)}</div></div><div class="wrap article-layout"><article class="prose">{body_html(content)}<section class="faq" id="faq"><h2>Frequently Asked Questions</h2>{faq_html}</section>
<section class="review-panel"><h2>Review this draft</h2><p>This sample is ready for your design and copy feedback. Local review does not approve publication.</p><div class="downloads"><a class="button" download href="article.md">Download article</a><a class="button light" download href="article.json">Download content package</a></div></section></article>
<aside class="sidebar"><div class="quote-box"><h2>Need Insulation Help?</h2><p>Get a free estimate for your San Antonio home.</p><a class="phone" href="tel:2108485658">210-848-5658</a><a class="button light" href="{SITE}contact-us/">Request a quote</a></div><nav class="contents" aria-label="On this page"><h2>In this article</h2>{toc}<a href="#faq">Frequently Asked Questions</a></nav></aside></div></main>'''
    (dest / "index.html").write_text(
        document(
            h1,
            header("../assets/", "../index.html") + article + schema_html + footer("../assets/"),
            "../assets/",
            fm["description"],
        )
    )
    summary = {
        "slug": slug,
        "title": h1,
        "description": fm["description"],
        "word_count": report.word_count,
        "image_alt": hero["alt"],
    }
    (dest / "preview.json").write_text(json.dumps(summary, indent=2))
    export_library(destination)
    return dest / "index.html"


def export_library(destination: Path) -> None:
    """List locally exported drafts; links work with or without a local server."""
    cards = []
    for path in sorted(destination.glob("*/preview.json")):
        item = json.loads(path.read_text())
        slug = path.parent.name
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            continue
        cards.append(
            f'''<article class="card"><a href="{slug}/index.html"><img src="{slug}/hero.webp" alt="{escape(item["image_alt"])}" width="1536" height="864"></a><div class="card-text"><span class="label">Local draft · {int(item["word_count"]):,} words</span><h2><a href="{slug}/index.html">{escape(item["title"])}</a></h2><p>{escape(item["description"])}</p><a class="button" href="{slug}/index.html">Review article →</a></div></article>'''
        )
    content = (
        '<main id="main" class="wrap library"><span class="label">Geo-Insulation · Local review</span><h1>Blog drafts</h1><p class="library-intro">Review new articles in the colors and typography of your current website. These drafts are saved on this computer and have not been published.</p><div class="cards">'
        + "".join(cards)
        + "</div></main>"
    )
    (destination / "index.html").write_text(
        document(
            "Blog drafts", header("assets/", "index.html") + content + footer("assets/"), "assets/"
        )
    )
