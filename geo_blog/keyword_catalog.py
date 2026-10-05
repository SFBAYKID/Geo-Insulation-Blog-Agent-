"""Adapt the owner's keyword table without modifying its schema or inventing metrics."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def grouped_topics(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group variants by topic AND target page; repeated phrases may serve different pages."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        fields = record.get("fields", {})
        if str(fields.get("Keyword", "")).strip():
            groups[(str(fields.get("Topic", "")), str(fields.get("Target Page", "")))].append(
                record
            )
    topics = []
    for (label, target), rows in groups.items():
        # A published variant consumes the whole topic group, including after a
        # local database restore. Publication status is never inferred from URLs.
        if any(
            str(row["fields"].get("Publication Status", "")).strip().casefold() == "published"
            for row in rows
        ):
            continue
        primary = next(
            (
                row
                for row in rows
                if not str(row["fields"].get("Notes", ""))
                .lower()
                .startswith("supporting variant of:")
            ),
            rows[0],
        )
        f = primary["fields"]
        keyword = str(f["Keyword"]).strip()
        supporting = list(
            dict.fromkeys(
                str(row["fields"]["Keyword"]).strip()
                for row in rows
                if str(row["fields"]["Keyword"]).strip().casefold() != keyword.casefold()
            )
        )
        # The target may be an existing service/homepage or a proposed page. It is
        # context for a new blog, never permission to overwrite that target URL.
        topics.append(
            {
                "id": primary["id"],
                "source_record_ids": [row["id"] for row in rows],
                "keyword": keyword,
                "secondary_keyword": supporting[0] if supporting else "",
                "supporting_keywords": supporting,
                "topic": label or keyword,
                "angle": f"Explain {label or keyword} for local homeowners. "
                + str(f.get("On-Page Target", "")),
                "notes": str(f.get("Notes", "")),
                "client_notes": str(f.get("Client Notes", "")),
                "target_page_context": target,
                "hub": "home-comfort",
                "target_words": 1200,
                "search_volume": f.get("Search Volume"),
                "priority": f.get("Priority"),
                "source_snapshot": rows,
                "metrics_source": "owner-supplied Airtable; not independently verified",
            }
        )
    return topics


def select_group(
    records: list[dict[str, Any]], used: set[str], record_id: str | None = None
) -> dict[str, Any] | None:
    """Use Airtable view order and preserve source record ownership across drafts."""
    topics = [
        topic for topic in grouped_topics(records) if not set(topic["source_record_ids"]) & used
    ]
    candidates = [
        topic for topic in topics if not record_id or record_id in topic["source_record_ids"]
    ]
    if not candidates:
        return None
    return dict(candidates[0], queue_remaining_after_selection=len(topics) - 1)
