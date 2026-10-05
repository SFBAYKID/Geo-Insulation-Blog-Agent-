"""Fetch Google organic results from SerpApi, preserving position and search context."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import httpx


class GoogleSearchUnavailable(RuntimeError):
    pass


def account_status(settings: Settings) -> Any:
    """Read only plan/quota fields; never persist or print the returned key."""
    key = settings.serpapi_api_key.get_secret_value()
    if not key:
        raise GoogleSearchUnavailable("Search key is not configured")
    logger = logging.getLogger("httpx")
    previous = logger.disabled
    logger.disabled = True
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get("https://serpapi.com/account.json", params={"api_key": key})
            response.raise_for_status()
            raw = response.json()
        return {
            name: raw.get(name)
            for name in (
                "account_status",
                "plan_name",
                "plan_monthly_price",
                "searches_per_month",
                "total_searches_left",
            )
        }
    except Exception:
        raise GoogleSearchUnavailable("Could not verify search plan or quota") from None
    finally:
        logger.disabled = previous


# Real guides title a "sample" as templates or examples and a "text message" as SMS.
# Exact-word matching rejected zcal, GoReminders and Curogram for "meeting reminder
# text message sample" and aborted the night (September 23, 2026).
SYNONYMS = {
    "template": "sample",
    "example": "sample",
    "sms": "message",
    "texts": "text",
}


def _terms(text: str) -> Any:
    terms = set()
    for word in re.findall(r"[a-z0-9]+", text.casefold()):
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        terms.add(SYNONYMS.get(word, word))
    return terms


def on_topic(title: str, keyword: str) -> bool:
    """At least half the keyword's words, allowing plurals and common synonyms, appear in the title."""
    words = {w for w in _terms(keyword) if len(w) > 2}
    return not words or 2 * len(words & _terms(title)) >= len(words)


def google_results(settings: Settings, keyword: str, client: Any = None) -> Any:
    """Google results."""
    key = settings.serpapi_api_key.get_secret_value()
    if not key:
        raise GoogleSearchUnavailable("Google search key is not configured")
    params = {
        "engine": "google",
        "q": keyword,
        "google_domain": "google.com",
        "location": settings.google_search_location,
        "gl": "us",
        "hl": "en",
        "device": "desktop",
        "no_cache": "true",
        "api_key": key,
    }
    # httpx INFO logs include query strings; never log the credential-bearing URL.
    logger = logging.getLogger("httpx")
    previous = logger.disabled
    logger.disabled = True
    try:
        if client is None:
            with httpx.Client(timeout=90) as session:
                response = session.get("https://serpapi.com/search.json", params=params)
        else:
            response = client.get("https://serpapi.com/search.json", params=params)
        response.raise_for_status()
        raw = response.json()
    except Exception:
        raise GoogleSearchUnavailable("Google search could not be retrieved") from None
    finally:
        logger.disabled = previous
    if raw.get("error") or raw.get("search_metadata", {}).get("status") != "Success":
        raise GoogleSearchUnavailable("Google search service did not complete the request")
    actual = raw.get("search_parameters", {})
    if (
        actual.get("engine") != "google"
        or actual.get("q", "").strip().casefold() != keyword.strip().casefold()
    ):
        raise GoogleSearchUnavailable("Search engine or keyword did not match")
    # Whitelist fields: do not save credentials, request objects or arbitrary provider metadata.
    results = []
    for item in raw.get("organic_results", []):
        url = item.get("link", "")
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not host or urlparse(url).scheme not in {"http", "https"}:
            continue
        kind = "organic_page"
        if host in {
            "reddit.com",
            "quora.com",
            "youtube.com",
            "youtu.be",
        } or host.startswith(("forum.", "forums.")):
            kind = "organic_forum_or_video"
        position = item.get("position")
        if not isinstance(position, int) or position < 1:
            raise GoogleSearchUnavailable("Organic ranking position missing")
        results.append(
            {
                "position": position,
                "url": url,
                "title": item.get("title", host),
                "kind": kind,
            }
        )
    if not results:
        raise GoogleSearchUnavailable("No organic Google results were returned")
    # A degraded scrape still returns 200 OK. Google served 53 total results and eight
    # unrelated pages for a normal query, leaving two eligible competitors and aborting
    # the night with no draft (September 20, 2026). Ask again instead of accepting it.
    hosts = {
        (urlparse(item["url"]).hostname or "").lower().removeprefix("www.")
        for item in results
        if item["kind"] in {"organic_article", "organic_page"}
    }
    if len(hosts - {"geo-insulation.com", "unrelated.example"}) < 3:
        raise GoogleSearchUnavailable("Too few eligible organic results to compare")
    # A degraded scrape can also return plenty of pages that are simply off-topic: a
    # Platformer story, an Instagram reel and schema.org for "conference follow up email
    # template" (September 22, 2026). A fresh request returned real guides.
    eligible = [item for item in results if item["kind"] in {"organic_article", "organic_page"}][:3]
    if sum(on_topic(item["title"], keyword) for item in eligible) < 2:
        logging.getLogger(__name__).warning(
            "Rejected off-topic results: %s", [item["title"] for item in eligible]
        )
        raise GoogleSearchUnavailable("Top organic results do not match the keyword")
    return {
        "source": "google_serpapi",
        "query": keyword,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "location": settings.google_search_location,
        "device": "desktop",
        "country": "us",
        "language": "en",
        "search_url": "https://www.google.com/search?"
        + urlencode({"q": keyword, "gl": "us", "hl": "en", "pws": "0"}),
        "provider_search_id": raw.get("search_metadata", {}).get("id"),
        "results": results,
    }
