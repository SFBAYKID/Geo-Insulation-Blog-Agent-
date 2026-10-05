"""Post draft-ready review requests once, without completing tasks or publishing."""

from __future__ import annotations

import hashlib
import html
import json
from typing import Any
from urllib.parse import urlparse

from .basecamp_api import Basecamp
from .basecamp_queue import TITLE, has_existing_draft, validate_basecamp_draft
from .settings import Settings
from .store import Store


def initialize(store: Store) -> None:
    """Keep external delivery reservations separate from Slack and writing state."""
    with store.db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS basecamp_reviews (
            event_key TEXT PRIMARY KEY, draft_id TEXT NOT NULL, state TEXT NOT NULL,
            task_id INTEGER NOT NULL, comment_id INTEGER, comment_url TEXT,
            error TEXT)""")


def preview_link(draft: dict[str, Any]) -> str:
    """Require the exact task's checked preview, not a production or arbitrary URL."""
    validate_basecamp_draft(draft)
    url = str(draft.get("preview_url", ""))
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not (parsed.hostname or "").endswith(".vercel.app")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != draft["topic"]["planned_url"]
        or draft.get("state") != "ready"
        or not draft.get("preview_commit")
        or not draft.get("production_lighthouse")
        or draft.get("report", {}).get("passed") is not True
    ):
        raise ValueError("Basecamp review requires a checked website preview for this task")
    return url


def draft_ready(settings: Settings, store: Store, draft_id: str) -> str | None:
    """Mention the reviewer on the original task; reconcile ambiguous POSTs without resending."""
    if not settings.basecamp_draft_ready_enabled:
        return None
    row = store.required(draft_id)
    if row["status"] not in {"ready", "pending", "approved", "rejected"} or not row.get("payload"):
        raise ValueError("Draft is not ready for Basecamp review")
    draft = json.loads(row["payload"])
    topic = draft.get("topic", {})
    if not topic.get("basecamp_task_id"):
        return None  # Explicit practice drafts and legacy Airtable drafts stay separate.
    url = preview_link(draft)
    task_id = int(topic["basecamp_task_id"])
    digest = hashlib.sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest()
    key = f"{settings.basecamp_account_id}:{settings.basecamp_project_id}:{task_id}:{digest}"
    marker = "the shared host draft-ready " + digest
    initialize(store)
    with store.db() as db:
        previous = db.execute("SELECT * FROM basecamp_reviews WHERE event_key=?", (key,)).fetchone()
    if previous and previous["state"] == "sent":
        return str(previous["comment_url"])
    with Basecamp(settings) as api:
        task = api.task(task_id)
        comments = api.comments(task_id)
        found = [c for c in comments if marker in c.get("content", "")]
        if len(found) > 1:
            raise ValueError("Duplicate Basecamp review markers require inspection")
        if found:
            with store.db() as db:
                db.execute(
                    "INSERT OR REPLACE INTO basecamp_reviews VALUES(?,?,'sent',?,?,?,NULL)",
                    (key, draft_id, task_id, found[0]["id"], found[0]["app_url"]),
                )
            return str(found[0]["app_url"])
        if previous:
            raise ValueError("Previous Basecamp send is unresolved; inspect before any retry")
        match = TITLE.fullmatch(str(task.get("content", "")))
        if (
            task.get("completed")
            or task.get("status") != "active"
            or not match
            or match[2] != topic["planned_url"]
            or match[3] != topic["keyword"]
            or task.get("updated_at") != topic.get("basecamp_task_updated_at")
            or has_existing_draft(comments)
        ):
            raise ValueError("Basecamp task changed or already has a draft; inspect before posting")
        reviewer = api.reviewer()
        content = (
            '<div><bc-attachment sgid="'
            + html.escape(reviewer["attachable_sgid"], quote=True)
            + '"></bc-attachment> — this blog draft is ready for your review.</div>'
            + "<div><strong>"
            + html.escape(draft["front_matter"]["title"])
            + "</strong></div>"
            + "<ul><li>Target keyword: "
            + html.escape(topic["keyword"])
            + "</li>"
            + '<li><a href="'
            + html.escape(url, quote=True)
            + '">Open draft preview</a></li>'
            + "<li>Status: Draft ready. Awaiting the reviewer’s review; not published.</li></ul>"
            + "<div>Please leave your feedback on this task. It will remain open.</div>"
            + "<div><em>"
            + marker
            + "</em></div>"
        )
        # A durable claim precedes POST. Any error, including a timeout, requires
        # reconciliation; no automatic retries or delete/reset path exists.
        with store.db() as db:
            if not db.execute(
                "INSERT OR IGNORE INTO basecamp_reviews VALUES(?,?,'sending',?,NULL,NULL,NULL)",
                (key, draft_id, task_id),
            ).rowcount:
                raise ValueError("Basecamp review delivery is already reserved")
        try:
            result = api.request(
                "POST",
                api.bucket + f"recordings/{task_id}/comments.json",
                json={"content": content},
            ).json()
            if not isinstance(result.get("id"), int) or not result.get("app_url"):
                raise ValueError("Basecamp send returned an incomplete receipt")
        except Exception as exc:
            with store.db() as db:
                db.execute(
                    "UPDATE basecamp_reviews SET error=? WHERE event_key=?",
                    (type(exc).__name__, key),
                )
            raise
        with store.db() as db:
            db.execute(
                "UPDATE basecamp_reviews SET state='sent',comment_id=?,comment_url=? WHERE event_key=?",
                (result["id"], result["app_url"], key),
            )
        return str(result["app_url"])
