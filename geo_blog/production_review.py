"""Exact-version review of the owner-approved production-channel handoff."""

from __future__ import annotations

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import Settings
from .slack_guard import safe_client

ACTIONS = {"geo_production_approve": "approved", "geo_production_reject": "rejected"}


def review_path(settings: Settings) -> Path:
    """Use a separate receipt; playground decisions cannot publish or replace this review."""
    return settings.storage_dir / "production-review.json"


def review_blocks(
    draft: dict[str, Any], commit: str, *, publishing: bool = False
) -> list[dict[str, Any]]:
    """Reuse the playground card verbatim; only internal action routing differs."""
    from .slack_app import review_blocks as shared_review_blocks

    blocks = shared_review_blocks(draft, commit)
    for block in blocks:
        if publishing and block.get("type") == "context":
            for element in block.get("elements", []):
                if element.get("text", "").startswith("Approve saves your decision"):
                    element["text"] = (
                        "Approve publishes this exact version to geo-insulation.com after final checks. I’ll confirm the live link here."
                    )
        if block.get("type") == "actions":
            for button in block["elements"]:
                button["action_id"] = {
                    "blog_approve": "geo_production_approve",
                    "blog_reject": "geo_production_reject",
                }[button["action_id"]]
    return blocks


def handle_review(settings: Settings, body: dict[str, Any], raw_client: Any) -> bool:
    """Require the authorized user, channel, message and immutable commit before saving."""
    path = review_path(settings)
    if not settings.production_delivery_enabled or not settings.slack_production_channel_id:
        return False
    if not path.exists():
        return False
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = json.loads(path.read_text())
        action = (body.get("actions") or [{}])[0]
        user = body.get("user", {}).get("id")
        if (
            body.get("team", {}).get("id") != settings.slack_team_id
            or body.get("channel", {}).get("id") != settings.slack_production_channel_id
            or body.get("message", {}).get("ts") != saved["message_ts"]
            or user not in settings.approvers
            or action.get("value") != saved["preview_commit"]
            or action.get("action_id") not in ACTIONS
            or saved["state"] != "pending"
        ):
            return False
        decision = ACTIONS[action["action_id"]]
        from .production_publish import comments_digest

        will_publish = decision == "approved" and saved.get("publication_armed") is True
        saved.update(
            state="queued" if will_publish else decision,
            reviewer=user,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            approved_comments_digest=comments_digest(saved),
        )
        path.write_text(json.dumps(saved, indent=2))
        blocks = [b for b in saved["blocks"] if b["type"] != "actions"]
        text = f"Review {'approved for publication' if decision == 'approved' else 'returned for changes'} by <@{user}>."
        text += (
            " Publication is queued. I’ll confirm the live link here after deployment and checks."
            if will_publish
            else " The article is not live yet; website publication is a separate step."
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
        client = safe_client(settings, raw_client, production_review_message_ts=saved["message_ts"])
        client.chat_update(
            channel=settings.slack_production_channel_id,
            ts=saved["message_ts"],
            text=text,
            blocks=blocks,
        )
        return True


def handle_comment(settings: Settings, event: dict[str, Any], raw_client: Any) -> bool:
    """Record explicitly mentioned reviewer comments only in the authorized blog thread."""
    path = review_path(settings)
    if not settings.production_delivery_enabled or not settings.slack_production_channel_id:
        return False
    if not path.exists() or event.get("type") != "app_mention":
        return False
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = json.loads(path.read_text())
        if (
            event.get("channel") != settings.slack_production_channel_id
            or event.get("thread_ts") != saved.get("thread_ts")
            or event.get("user") not in settings.approvers
            or not event.get("ts")
        ):
            return False
        comments = saved.setdefault("comments", [])
        if any(c["ts"] == event["ts"] for c in comments):
            return True
        held = saved.get("state") in {"queued", "publishing"}
        if held:
            saved["state"] = "pending"
        comments.append(
            {
                "ts": event["ts"],
                "user": event["user"],
                "text": event.get("text", ""),
                "status": "recorded",
            }
        )
        path.write_text(json.dumps(saved, indent=2))
        if held:
            safe_client(
                settings, raw_client, production_review_message_ts=saved["message_ts"]
            ).chat_update(
                channel=settings.slack_production_channel_id,
                ts=saved["message_ts"],
                text="New feedback received; publication is held for a fresh review.",
                blocks=saved["blocks"],
            )
        client = safe_client(settings, raw_client, production_share=True)
        client.chat_postMessage(
            channel=settings.slack_production_channel_id,
            thread_ts=saved["thread_ts"],
            text="Thanks—I’ve recorded your feedback for this draft. The current preview has not changed yet.",
        )
        return True
