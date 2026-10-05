"""Read the actual Geo Insulation website rather than guessing identity from search."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

SERVICE_PATHS = (
    "/attic-insulation-in-san-antonio/",
    "/attic-insulation-removal/",
    "/blown-in-insulation-for-san-antonio-homes/",
    "/fiberglass-insulation/",
    "/garage-insulation-in-san-antonio/",
    "/radiant-barrier/",
    "/roll-and-batt-insulation/",
    "/spray-foam-insulation/",
    "/attic-de-cluttering-and-disposal/",
)
URLS = tuple(
    "https://geo-insulation.com" + path
    for path in ("/", "/about-us/", "/contact-us/", "/blog/", *SERVICE_PATHS)
)


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        """Handle starttag."""
        if tag in {"script", "style", "nav", "footer"}:
            self.skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        """Handle endtag."""
        if tag in {"script", "style", "nav", "footer"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: Any) -> None:
        """Handle data."""
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def company_evidence(topic: dict[str, Any] | None = None) -> Any:
    """Company evidence."""
    pages = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        urls = list(URLS)
        terms = set(re.findall(r"[a-z]{3,}", str((topic or {}).get("keyword", "")).lower())) - {
            "for",
            "the",
            "and",
            "with",
        }
        for url in urls:
            response = client.get(url)
            if response.status_code in {404, 410} and url not in URLS:
                continue
            response.raise_for_status()
            if urlparse(str(response.url)).hostname != "geo-insulation.com":
                raise ValueError("Company source redirected to another domain")
            parser = TextParser()
            parser.feed(response.text)
            if "noindex" in response.text.lower() and url not in URLS:
                continue
            pages.append({"url": str(response.url), "text": " ".join(parser.parts)[:16000]})
            if url.rstrip("/") == "https://geo-insulation.com/blog" and terms:
                candidates = list(
                    {urljoin(url, href).split("#")[0].split("?")[0] for href in parser.links}
                )
                candidates = [
                    u
                    for u in candidates
                    if urlparse(u).hostname == "geo-insulation.com"
                    and re.fullmatch(r"/blog/(?:[^/]+/)?[^/]+/?", urlparse(u).path)
                ]

                def score(u: str) -> int:
                    return len(terms & set(re.findall(r"[a-z]{3,}", urlparse(u).path)))

                matches = [u for u in candidates if score(u)]
                urls.extend(sorted(matches or candidates, key=lambda u: (-score(u), u))[:3])
    return pages
