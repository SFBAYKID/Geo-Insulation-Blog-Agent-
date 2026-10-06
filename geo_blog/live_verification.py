"""Verify live Astro content before marking its source brief complete."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from .content import parse_draft
from .site_export import blocks
from .site_faq import extract_faq

ORIGIN = "https://geo-insulation.com"


def verify_article(draft: dict[str, Any], client: httpx.Client) -> str:
    """Require matching metadata, visible FAQs, schema, image bytes and sitemap URL."""
    fm, markdown = parse_draft(draft["markdown"])
    slug = fm["slug"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Invalid live article slug")
    body, faqs = extract_faq(markdown.replace("—", " - "))
    headline, _ = blocks(body)
    url = f"{ORIGIN}/blog/{slug}/"
    response = client.get(url)
    if response.status_code != 200 or str(response.url) != url:
        raise ValueError("Live article URL did not return the expected page")
    soup = BeautifulSoup(response.text, "html.parser")
    description = soup.select_one('meta[name="description"]')
    canonical = soup.select_one('link[rel="canonical"]')
    robots = " ".join(str(tag.get("content", "")) for tag in soup.select('meta[name="robots"]'))
    if (
        soup.title is None
        or soup.title.get_text() != fm["title"]
        or description is None
        or description.get("content") != fm["description"]
        or canonical is None
        or canonical.get("href") != url
        or "noindex" in (robots + response.headers.get("x-robots-tag", "")).lower()
        or len(soup.select("h1")) != 1
        or soup.select("h1")[0].get_text(strip=True) != headline
    ):
        raise ValueError("Live article metadata differs from the reviewed version")
    hero = draft["topic"]["hero"]
    image_path = f"/blog/media/{slug}-hero.webp"
    caption = hero.get("caption") or draft["media_provenance"].get("caption", "")
    images = [img for img in soup.select("main img") if img.get("src") == image_path]
    article = soup.select_one("article")
    if (
        hero["src"] != image_path
        or len(images) != 1
        or images[0].get("alt") != hero["alt"]
        or not str(images[0].get("width", "")).isdigit()
        or not str(images[0].get("height", "")).isdigit()
        or int(str(images[0].get("width", "0"))) <= 0
        or int(str(images[0].get("height", "0"))) <= 0
        or article is None
        or not caption
        or caption not in article.get_text()
        or (
            draft["media_provenance"].get("origin") == "generated"
            and not caption.startswith("Illustration: ")
        )
    ):
        raise ValueError("Live article image or illustration label is incorrect")
    image = client.get(ORIGIN + image_path)
    if (
        image.status_code != 200
        or hashlib.sha256(image.content).hexdigest() != draft["media_provenance"]["sha256"]
    ):
        raise ValueError("Live image differs from the reviewed image")
    schemas: list[dict[str, Any]] = []
    for tag in soup.select('script[type="application/ld+json"]'):
        value = json.loads(tag.get_text())
        schemas.extend(value if isinstance(value, list) else [value])
    faq_schema = [schema for schema in schemas if schema.get("@type") == "FAQPage"]
    visible = []
    for detail in article.select("details"):
        question, answer = detail.select_one("summary"), detail.select_one("p")
        if question is None or answer is None:
            raise ValueError("Live FAQ markup is incomplete")
        visible.append({"question": question.get_text(), "answer": answer.get_text()})
    if len(faq_schema) != 1 or visible != faqs:
        raise ValueError("Live FAQ text differs from the approved article")
    structured = [
        {"question": item.get("name"), "answer": item.get("acceptedAnswer", {}).get("text")}
        for item in faq_schema[0].get("mainEntity", [])
    ]
    if structured != faqs:
        raise ValueError("Live FAQ schema differs from its visible answers")
    sitemap = client.get(ORIGIN + "/sitemap.xml")
    if sitemap.status_code != 200:
        raise ValueError("Live sitemap is unavailable")
    locations = {
        node.text
        for node in ElementTree.fromstring(sitemap.text).iter()
        if node.tag.rsplit("}", 1)[-1] == "loc"
    }
    if url not in locations:
        raise ValueError("Live article is missing from the sitemap")
    return url
