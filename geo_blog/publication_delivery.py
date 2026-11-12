"""Durable business-hours announcements and article-specific Undo cards."""

from __future__ import annotations

import fcntl
import json
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from .settings import Settings
from .slack_guard import safe_client


class OutsideAnnouncementWindow(ValueError):
    """A new production post was stopped before contacting Slack."""


def business_hours(now: datetime | None = None) -> bool:
    """Use Central local time, including DST; never announce on weekends."""
    current = (now or datetime.now(ZoneInfo("America/Chicago"))).astimezone(
        ZoneInfo("America/Chicago")
    )
    return current.weekday() < 5 and 9 <= current.hour < 17


def publication_path(settings: Settings, draft_id: str) -> Path:
    """Keep each publication addressable after the next weekly blog is produced."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", draft_id):
        raise ValueError("Invalid publication ID")
    folder = settings.storage_dir / "publications"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (draft_id + ".json")


@contextmanager
def locked_publication(path: Path) -> Iterator[dict[str, Any]]:
    """Serialize notification and Undo receipts, writing atomically on success."""
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = json.loads(path.read_text()) if path.exists() else {}
        yield record
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2))
        temporary.replace(path)


def published_blocks(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Offer only Undo publication; this card never asks for approval."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{record['title']}*\nPublished: <{record['live_url']}|Read the blog>.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Undo publication"},
                    "action_id": "geo_publication_undo",
                    "value": record["draft_id"] + ":" + record["production_commit"],
                    "style": "danger",
                }
            ],
        },
    ]


def save_publication(settings: Settings, review: dict[str, Any], draft: dict[str, Any]) -> None:
    """Persist notification and Undo state before any Slack operation."""
    path = publication_path(settings, review["draft_id"])
    with locked_publication(path) as record:
        if record:
            if record["production_commit"] != review["production_commit"]:
                raise ValueError("Publication receipt commit mismatch")
            return
        record.update(
            draft_id=review["draft_id"],
            production_commit=review["production_commit"],
            preview_commit=review["preview_commit"],
            live_url=review["live_url"],
            title=draft["front_matter"]["title"],
            slug=draft["front_matter"]["slug"],
            state="published",
            notification_state="pending",
            message_ts=review.get("message_ts", ""),
            thread_ts=review.get("thread_ts", ""),
        )


def deliver_pending(settings: Settings) -> None:
    """Update an existing card in place; defer new posts until Central business hours."""
    folder = settings.storage_dir / "publications"
    for path in sorted(folder.glob("*.json")):
        with locked_publication(path) as record:
            if record.get("state") != "published" or record.get("notification_state") != "pending":
                continue
            if not record.get("message_ts") and not business_hours():
                continue
            # Persist sending before network I/O: uncertain posts are never duplicated.
            record["notification_state"] = "sending"
            path.write_text(json.dumps(record, indent=2))
            text = f"Published: {record['title']}. {record['live_url']}"
            client = safe_client(
                settings,
                production_share=True,
                production_review_message_ts=record.get("message_ts", ""),
            )
            try:
                if record.get("message_ts"):
                    client.chat_update(
                        channel=settings.slack_production_channel_id,
                        ts=record["message_ts"],
                        text=text,
                        blocks=published_blocks(record),
                    )
                    if record.get("thread_ts") and record["thread_ts"] != record["message_ts"]:
                        safe_client(
                            settings, production_review_message_ts=record["thread_ts"]
                        ).chat_update(
                            channel=settings.slack_production_channel_id,
                            ts=record["thread_ts"],
                            text=f"Published: {record['title']}. The live link and Undo publication button are in this thread.",
                            blocks=[],
                        )
                else:
                    result = client.chat_postMessage(
                        channel=settings.slack_production_channel_id,
                        text=text,
                        blocks=published_blocks(record),
                    )
                    record["message_ts"] = result["ts"]
                record["notification_state"] = "sent"
            except OutsideAnnouncementWindow:
                record["notification_state"] = "pending"
            except Exception as exc:
                record.update(
                    notification_state="needs_inspection", notification_error=type(exc).__name__
                )
                from .weekly import private_alert

                private_alert(
                    settings,
                    "The blog is published, but its Slack announcement needs inspection. No automatic resend will occur.",
                )
