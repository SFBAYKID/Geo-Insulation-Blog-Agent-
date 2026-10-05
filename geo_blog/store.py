"""Durable single-host daily runs and exact-draft approval records."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import sqlite3

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


class Store:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "blog.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, run_key TEXT UNIQUE NOT NULL,
                    topic_key TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT, channel TEXT, message_ts TEXT,
                    reviewer TEXT, reviewed_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS publish_jobs (
                    id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, state TEXT NOT NULL,
                    payload TEXT NOT NULL, reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                    message_ts TEXT NOT NULL UNIQUE, channel TEXT NOT NULL, thread_ts TEXT NOT NULL,
                    live_url TEXT, notification_ts TEXT, error TEXT);
            """)
            if "thread_ts" not in {r[1] for r in db.execute("PRAGMA table_info(drafts)")}:
                db.execute("ALTER TABLE drafts ADD COLUMN thread_ts TEXT")

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        """Open a transaction and always close its connection, including on failure."""
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, draft_id: str, run_key: str, topic_key: str) -> bool:
        """Atomically reserve a unique run before any paid or external operation."""
        with self.db() as db:
            return (
                db.execute(
                    "INSERT OR IGNORE INTO drafts(id,run_key,topic_key,status) VALUES(?,?,?,'writing')",
                    (draft_id, run_key, topic_key),
                ).rowcount
                == 1
            )

    def get(self, draft_id: str) -> dict[str, Any] | None:
        """Read a draft snapshot, returning None for an unknown identifier."""
        with self.db() as db:
            row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            return dict(row) if row else None

    def required(self, draft_id: str) -> dict[str, Any]:
        """Read an existing reservation or fail explicitly instead of dereferencing None."""
        row = self.get(draft_id)
        if row is None:
            raise ValueError("Unknown draft")
        return row

    def used_topics(self) -> set[str]:
        """Return all reserved source IDs, including variants in saved groups."""
        with self.db() as db:
            used = set()
            for row in db.execute("SELECT topic_key,payload FROM drafts"):
                used.add(row[0])
                if row[1]:
                    used.update(json.loads(row[1]).get("topic", {}).get("source_record_ids", []))
                    keyword = (
                        json.loads(row[1]).get("topic", {}).get("keyword", "").strip().casefold()
                    )
                    if keyword:
                        used.add("keyword:" + keyword)
            return used

    def save(self, draft_id: str, payload: dict[str, Any]) -> None:
        """Persist the generated payload only while the reserved draft is still being written."""
        with self.db() as db:
            db.execute(
                "UPDATE drafts SET payload=?,status='ready' WHERE id=? AND status='writing'",
                (json.dumps(payload), draft_id),
            )

    def claim_delivery(self, draft_id: str, channel: str) -> bool:
        """Atomically mark a ready draft as sending before contacting Slack."""
        with self.db() as db:
            return (
                db.execute(
                    "UPDATE drafts SET status='sending',channel=? WHERE id=? AND status='ready'",
                    (channel, draft_id),
                ).rowcount
                == 1
            )

    def attach_preview(self, draft_id: str, preview_url: Any, qa_summary: Any) -> None:
        """Attach a verified preview only to an unsent ready draft."""
        from urllib.parse import urlparse

        parsed = urlparse(preview_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".vercel.app")
        ):
            raise ValueError("Expected an HTTPS Vercel preview URL")
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM drafts WHERE id=? AND status='ready'", (draft_id,)
            ).fetchone()
            if not row:
                raise ValueError("Only an unsent ready draft can receive a preview")
            payload = json.loads(row[0])
            payload.update(preview_url=preview_url, qa_summary=qa_summary)
            db.execute(
                "UPDATE drafts SET payload=? WHERE id=?",
                (json.dumps(payload), draft_id),
            )

    def set_thread(self, draft_id: str, channel: str, thread_ts: str) -> None:
        """Bind a draft to a valid Slack timestamp without allowing a later thread switch."""
        import re

        if not re.fullmatch(r"\d+\.\d+", thread_ts):
            raise ValueError("Expected a Slack message timestamp")
        with self.db() as db:
            changed = db.execute(
                "UPDATE drafts SET channel=?,thread_ts=? WHERE id=? "
                "AND status IN ('writing','ready','sending') AND (thread_ts IS NULL OR thread_ts=?)",
                (channel, thread_ts, draft_id, thread_ts),
            ).rowcount
            if not changed:
                raise ValueError("Thread cannot be changed for this draft")

    def delivered(self, draft_id: str, ts: Any) -> None:
        """Record the exact review-card timestamp after a successful send."""
        with self.db() as db:
            db.execute(
                "UPDATE drafts SET status='pending',message_ts=? WHERE id=? AND status='sending'",
                (ts, draft_id),
            )

    def fail(self, draft_id: str, error: str) -> None:
        """Retain a failure reason and reservation for explicit recovery."""
        with self.db() as db:
            db.execute(
                "UPDATE drafts SET status='failed',error=? WHERE id=?",
                (error, draft_id),
            )

    def decide(
        self,
        draft_id: str,
        *,
        decision: str,
        user: str,
        channel: str,
        message_ts: str,
        allowed_users: set[str],
        publish: bool = False,
    ) -> bool:
        """Accept only the configured reviewer and the exact pending review card."""
        if user not in allowed_users or decision not in {"approved", "rejected"}:
            return False
        with self.db() as db:
            changed = (
                db.execute(
                    "UPDATE drafts SET status=?,reviewer=?,reviewed_at=? "
                    "WHERE id=? AND status='pending' AND channel=? AND message_ts=?",
                    (
                        decision,
                        user,
                        datetime.now(timezone.utc).isoformat(),
                        draft_id,
                        channel,
                        message_ts,
                    ),
                ).rowcount
                == 1
            )

            if changed and decision == "approved" and publish:
                from .publishing import queue_in_transaction

                row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
                queue_in_transaction(db, row)
            return changed
