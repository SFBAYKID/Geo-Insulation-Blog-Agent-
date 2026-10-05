"""Topics utilities for the Geo Insulation blog agent."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings
import json
import re
from urllib.parse import quote

import httpx


def secondary_keyword_from_brief(value: Any) -> str:
    """Read the first phrase, skipping headings and research annotations."""
    for line in str(value or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•]+\s*|\d+[.)]\s*)", "", line).strip(" #*\t")
        label = r"(?:measured\s+)?(?:supporting|secondary|related)\s+keywords?"
        line = re.sub(r"^" + label + r"\s*:\s*", "", line, flags=re.I)
        if not line or re.fullmatch(label + r"\s*:?", line, re.I):
            continue
        # Everything after a keyword's metric delimiter is evidence, not a phrase.
        phrase = re.split(r";|\s*\(|\s+[—–|]\s+", line)[0].strip().strip('"“”')
        if (
            not phrase
            or len(phrase) > 100
            or len(phrase.split()) > 12
            or not re.search(r"[a-zA-Z]", phrase)
            or re.search(r"[:.!?=]|https?://", phrase)
            or re.fullmatch(
                r"(?:tbd|n/?a|none|pending|unknown|not (?:available|measured)|"
                r"organic kd.*|cpc.*|paid competition.*|volume.*)",
                phrase,
                re.I,
            )
        ):
            continue
        return phrase
    return ""


def topic_from_record(record: dict[str, Any]) -> Any:
    """Adapt the legacy brief schema while preserving its source record identity."""
    f = record["fields"]
    topic = {
        "id": record["id"],
        "keyword": f["Primary keyword"],
        "topic": f.get("Working title") or f.get("Topic") or f["Primary keyword"],
        "angle": f.get("Angle") or f.get("Notes / rationale", ""),
        "priority": f.get("Priority", 0),
    }
    for field, key in [
        ("Supporting keywords", "supporting_keywords_brief"),
        ("Must include", "must_include"),
        ("Planned URL", "planned_url"),
        ("Target size (words)", "target_size_brief"),
    ]:
        if f.get(field):
            topic[key] = f[field]
    supporting = secondary_keyword_from_brief(f.get("Supporting keywords"))
    if supporting:
        topic["secondary_keyword"] = supporting
    size = str(f.get("Target size (words)", "")).replace(",", "")
    match = re.match(r"\s*(\d+)(?:\s*[-–—]\s*(\d+))?(?=\s|$)", size)
    if match:
        lo, hi = int(match[1]), int(match[2] or match[1])
        if not 800 <= lo <= hi <= 10000:
            raise ValueError("Invalid target word range in Airtable")
        topic["target_words"] = (lo + hi) // 2
    if f.get("Hub"):
        hub = re.sub(r"^/blog/", "", str(f["Hub"])).strip("/")
        if hub not in {
            "attic-insulation",
            "installation-process",
            "air-sealing",
            "energy-efficiency",
            "home-comfort",
        }:
            raise ValueError("Unknown Geo Insulation hub in Airtable")
        topic["hub"] = hub
    return topic


class IncompleteKeywordQueue(ValueError):
    """Unwritten rows exist but none has both usable keyword fields."""


class HeldKeywordQueue(ValueError):
    """Unused topics exist, but editorial status holds them back."""


def select_topic(
    settings: Settings, used: set[str], client: Any = None, record_id: Any = None
) -> Any:
    """Read user-entered keywords; never perform keyword/volume research."""
    if settings.blog_queue_source == "basecamp":
        from .basecamp_queue import select_basecamp_topic

        return select_basecamp_topic(settings, used, record_id)
    if settings.airtable_base_id:
        settings.require("airtable_token")
        endpoint = (
            "https://api.airtable.com/v0/"
            + quote(settings.airtable_base_id, safe="")
            + "/"
            + quote(settings.airtable_table, safe="")
        )
        params: dict[str, Any] = {"pageSize": 100}
        candidates = []
        current_records = []
        incomplete = set()
        held = set()
        completed_keywords = set()
        with httpx.Client(timeout=30) if client is None else client as http:
            while True:
                response = http.get(
                    endpoint,
                    params=params,
                    headers={
                        "Authorization": "Bearer " + settings.airtable_token.get_secret_value()
                    },
                )
                response.raise_for_status()
                data = response.json()
                for record in data.get("records", []):
                    current_records.append(record)
                    f = record.get("fields", {})
                    keyword_key = str(f.get("Primary keyword", "")).strip().casefold()
                    if f.get("Status", "").strip().casefold() in {
                        "published",
                        "draft",
                        "written",
                        "writing",
                        "in progress",
                        "in review",
                        "approved",
                        "done",
                        "completed",
                    }:
                        completed_keywords.add(keyword_key)
                    if (
                        f.get("Status", "").strip().casefold() == "retarget needed"
                        and record["id"] not in used
                        and "keyword:" + keyword_key not in used
                    ):
                        held.add(record["id"])
                    # Proposed/blank are now user-authorized candidates. Never overwrite done work.
                    if f.get("Status", "").strip().casefold() not in {
                        "",
                        "ready",
                        "proposed",
                        "not started",
                        "todo",
                        "to do",
                    }:
                        continue
                    if (
                        record["id"] in used
                        or "keyword:" + keyword_key in used
                        or (record_id and record["id"] != record_id)
                    ):
                        continue
                    if "Keyword" in f:
                        continue
                    if not str(f.get("Primary keyword", "")).strip():
                        if f.get("Working title") or f.get("Topic") or f.get("Supporting keywords"):
                            incomplete.add(record["id"])
                        continue
                    candidate = topic_from_record(record)
                    if not candidate.get("secondary_keyword"):
                        incomplete.add(record["id"])
                        continue
                    candidate["airtable_status"] = f.get("Status", "")
                    candidates.append(candidate)
                if not data.get("offset"):
                    break
                params["offset"] = data["offset"]
            # Read the full table above to exclude keywords completed outside the view.
            # The user's view defines the actual top-to-bottom drafting order.
            if settings.airtable_view:
                view_ids: list[str] = []
                params = {"pageSize": 100, "view": settings.airtable_view}
                while True:
                    response = http.get(
                        endpoint,
                        params=params,
                        headers={
                            "Authorization": "Bearer " + settings.airtable_token.get_secret_value()
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    view_ids.extend(record["id"] for record in data.get("records", []))
                    if not data.get("offset"):
                        break
                    params["offset"] = data["offset"]
                positions = {key: index for index, key in enumerate(view_ids)}
                incomplete.intersection_update(positions)
                held.intersection_update(positions)
                candidates = sorted(
                    (c for c in candidates if c["id"] in positions),
                    key=lambda c: positions[c["id"]],
                )
        if any("Keyword" in r.get("fields", {}) for r in current_records):
            from .keyword_catalog import select_group

            records = current_records
            if settings.airtable_view:
                records = sorted(
                    (r for r in records if r["id"] in positions),
                    key=lambda r: positions[r["id"]],
                )
            return select_group(records, used, record_id)
        candidates = [
            c for c in candidates if c["keyword"].strip().casefold() not in completed_keywords
        ]
        # Count distinct usable topics, not duplicate rows for the same phrase.
        unique: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            unique.setdefault(candidate["keyword"].strip().casefold(), candidate)
        candidates = list(unique.values())
        if candidates:
            topic = candidates[0]
            topic["queue_remaining_after_selection"] = len(candidates) - 1
            return topic
        if incomplete:
            raise IncompleteKeywordQueue(
                "Unwritten rows are missing a primary or usable supporting keyword"
            )
        if held:
            raise HeldKeywordQueue("Remaining topics are marked Retarget needed")
        return None
    topics = json.loads(Path("config/topics.json").read_text())
    return next(
        (
            dict(t, id=t["keyword"].casefold())
            for t in topics
            if t["keyword"].casefold() not in used
        ),
        None,
    )
