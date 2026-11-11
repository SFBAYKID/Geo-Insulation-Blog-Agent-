"""Read approved research first, hold potential overlaps, and retain Basecamp fallback."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from .research_api import COVERAGE, ResearchAPI
from .settings import Settings


def normalized(value: Any) -> str:
    """Compare complete keyword phrases without treating shared words as duplicates."""
    return " ".join(str(value or "").casefold().split())


def page_path(value: Any) -> str:
    """Normalize only Geo URLs and local absolute paths; reject foreign destinations."""
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.netloc and parsed.hostname not in {"geo-insulation.com", "www.geo-insulation.com"}:
        return ""
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return ""
    return parsed.path.rstrip("/") or "/"


def live_paths() -> set[str]:
    """Read bounded same-site sitemap documents; no failures are treated as no coverage."""
    pending = ["https://geo-insulation.com/sitemap.xml"]
    visited: set[str] = set()
    paths: set[str] = set()
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        while pending:
            url = pending.pop()
            if url in visited:
                continue
            if len(visited) >= 20 or urlparse(url).hostname != "geo-insulation.com":
                raise ValueError("Invalid Geo sitemap")
            visited.add(url)
            response = client.get(url)
            if response.status_code != 200 or len(response.content) > 2_000_000:
                raise ValueError("Live Geo sitemap could not be verified")
            root = ET.fromstring(response.content)
            kind = root.tag.rsplit("}", 1)[-1]
            locations = [
                node.text or "" for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "loc"
            ]
            if kind == "sitemapindex":
                pending.extend(locations)
            elif kind == "urlset":
                paths.update(filter(None, (page_path(url) for url in locations)))
            else:
                raise ValueError("Unknown sitemap format")
    return paths


def basecamp_coverage(settings: Settings) -> list[dict[str, Any]]:
    """Read open/completed direct requests in the authorized list without changing them."""
    from .basecamp_api import Basecamp
    from .basecamp_queue import TITLE, plain

    result = []
    with Basecamp(settings) as api:
        path = api.bucket + f"todolists/{settings.basecamp_todolist_id}/todos.json"
        rows = api.listing(path) + api.listing(path + "?completed=true")
        for row in rows:
            title = str(row.get("content", ""))
            match = TITLE.fullmatch(title)
            description = plain(str(row.get("description", "")))
            keyword = match[3] if match else ""
            planned = match[2] if match else ""
            if not keyword:
                found = re.search(r"(?:Primary|Main|Target) keyword:\s*([^\n]+)", description, re.I)
                keyword = found[1].strip() if found else ""
            if not planned:
                found = re.search(r"(?:Planned|Target) URL:\s*(\S+)", description, re.I)
                planned = found[1] if found else ""
            result.append(
                {
                    "Primary keyword": keyword,
                    "Published URL": planned,
                    "request_url": row.get("app_url", ""),
                }
            )
    return result


def overlap(fields: dict[str, Any], coverage: list[dict[str, Any]], paths: set[str]) -> str:
    """Flag exact phrase or planned-route matches for human intent review, never reject."""
    keyword = normalized(fields.get("Primary keyword"))
    planned = page_path(fields.get("Planned URL"))
    if planned and planned in paths:
        return (
            "Possible overlap with live page https://geo-insulation.com"
            + planned
            + "; review search intent."
        )
    for item in coverage:
        url = item.get("Published URL") or item.get("Planned URL") or ""
        if (keyword and keyword == normalized(item.get("Primary keyword"))) or (
            planned and planned == page_path(url)
        ):
            return (
                "Possible overlap with "
                + str(item.get("request_url") or url or item.get("Primary keyword"))
                + "; review search intent before reproposing."
            )
    return ""


def rank(row: dict[str, Any]) -> tuple[float, datetime, str]:
    """Apply default priority 100 and real UTC creation order, not a view's order."""
    fields = row["fields"]
    priority = fields.get("Priority", 100)
    if priority is None or priority == "":
        priority = 100
    number = float(priority)
    import math

    if not math.isfinite(number):
        raise ValueError("Invalid topic priority")
    created = datetime.fromisoformat(
        str(fields.get("Created at") or row.get("createdTime", "")).replace("Z", "+00:00")
    )
    if created.tzinfo is None:
        raise ValueError("Research topic creation time needs a timezone")
    return number, created, row["id"]


def select_research(
    settings: Settings,
    used: set[str],
    record_id: str | None = None,
    *,
    hold_duplicates: bool = False,
) -> dict[str, Any] | None:
    """Keep inspection read-only; only the drafting path may hold overlapping proposals."""
    from .basecamp_queue import select_basecamp_topic
    from .topics import topic_from_record

    with ResearchAPI(settings) as api:
        rows = api.rows()
        candidates = sorted(
            [
                r
                for r in rows
                if r["fields"].get("Status") == "Proposed"
                and r["id"] not in used
                and "keyword:" + normalized(r["fields"].get("Primary keyword")) not in used
            ],
            key=rank,
        )
        coverage = [r["fields"] for r in api.rows(COVERAGE)]
        coverage.extend(
            r["fields"]
            for r in rows
            if r["fields"].get("Status") == "Published" and r["fields"].get("Published URL")
        )
        coverage.extend(
            r["fields"]
            for r in rows
            if r["fields"].get("Status") in {"Held", "Claimed", "Drafting", "Review", "Rejected"}
        )
        coverage.extend(basecamp_coverage(settings))
        paths = live_paths()
        for row in candidates:
            if record_id and row["id"] != record_id:
                continue
            reason = overlap(row["fields"], coverage, paths)
            if reason:
                if hold_duplicates:
                    current = api.get(row["id"])
                    if current["fields"] == row["fields"]:
                        api.patch(row["id"], {"Status": "Held", "Hold reason": reason})
                continue
            fields = row["fields"]
            if not str(fields.get("Primary keyword", "")).strip():
                raise ValueError("Proposed topic needs a primary keyword")
            # Target size is advisory; legacy conversion imposes an unwanted hard range.
            adapted = {
                **row,
                "fields": {k: v for k, v in fields.items() if k != "Target size (words)"},
            }
            topic = dict(topic_from_record(adapted))
            if not topic.get("secondary_keyword"):
                raise ValueError("Proposed topic needs a usable first supporting keyword")
            # Hold a topic no draft can pass before spending model calls on it, and
            # move on to the next Proposed row in the same run.
            from .quality_failures import (
                MAX_QUALITY_FAILURES,
                failure_count,
                hold_reason,
                last_blockers,
            )
            from .writer_feedback import topic_blockers

            reason = ""
            if blockers := topic_blockers(topic["keyword"]):
                reason = (
                    " ".join(blockers)
                    + " Shorten the primary keyword, then set Status back to Proposed."
                )
            # Re-proposed unchanged after repeated failures (by a person or the research
            # agent's sync): it would fail the same way, so hold it again for free.
            elif (failures := failure_count(settings, row["id"], fields)) >= MAX_QUALITY_FAILURES:
                reason = hold_reason(topic, failures, last_blockers(settings, row["id"], fields))
            if reason:
                if hold_duplicates:
                    current = api.get(row["id"])
                    if current["fields"] == row["fields"]:
                        api.patch(row["id"], {"Status": "Held", "Hold reason": reason})
                continue
            topic.update(
                research_record_id=row["id"],
                research_source_fields=fields,
                notes=fields.get("Notes / rationale", ""),
                supporting_keywords=[
                    line.strip()
                    for line in str(fields.get("Supporting keywords", "")).splitlines()
                    if line.strip()
                ],
                target_size_brief=fields.get("Target size (words)", ""),
                queue_remaining_after_selection=len(candidates) - 1,
            )
            return topic
        if record_id and record_id.startswith("rec"):
            return None
        fallback = select_basecamp_topic(settings, used, record_id)
        if fallback:
            source_fields = {
                "Primary keyword": fallback["keyword"],
                "Planned URL": fallback.get("planned_url"),
            }
            blocked = [r["fields"] for r in rows]
            if overlap(source_fields, blocked, set()):
                raise ValueError(
                    "Basecamp request overlaps a held or already-owned research topic; inspect intent"
                )
        return fallback
