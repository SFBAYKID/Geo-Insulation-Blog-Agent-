"""Run an explicitly scheduled, single draft with durable duplicate protection.

launchd polls this entrypoint; timezone-aware due checks make it indepenair leak of
Mac clock preferences. Failed or interrupted attempts require manual inspection.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .settings import Settings
from .store import Store


def tick(settings: Settings, store: Store, plan: dict[str, Any], now: datetime) -> str:
    """Claim a due plan once; never backfill a stale draft or replay external calls."""
    due = datetime.fromisoformat(plan["run_at"])
    if due.tzinfo is None or now.tzinfo is None:
        raise ValueError("Schedule timestamps must have explicit timezones")
    if plan["channel"] != settings.slack_test_channel_id:
        raise ValueError("Scheduled drafts are restricted to the playground")
    if settings.slack_channel_id != plan["channel"]:
        raise ValueError("Schedule channel does not match the active channel")
    if not plan.get("enabled") or now < due:
        return "waiting"
    with (store.root / "draft-request.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with store.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS scheduled_drafts (
                id TEXT PRIMARY KEY, run_at TEXT NOT NULL, state TEXT NOT NULL,
                draft_id TEXT, error TEXT)""")
            # Claim before selecting a keyword or making a network/paid request.
            state = "expired" if now > due + timedelta(hours=2) else "working"
            if not db.execute(
                "INSERT OR IGNORE INTO scheduled_drafts VALUES(?,?,?,NULL,NULL)",
                (plan["id"], plan["run_at"], state),
            ).rowcount:
                return "already_claimed"
        if state == "expired":
            logging.error("Scheduled draft missed its two-hour execution window")
            return state
        try:
            from .cli import run_daily

            draft_id = run_daily(settings, store, send=True, request_key="schedule:" + plan["id"])
            state = "complete" if draft_id else "empty"
            with store.db() as db:
                db.execute(
                    "UPDATE scheduled_drafts SET state=?,draft_id=? WHERE id=?",
                    (state, draft_id, plan["id"]),
                )
            return state
        except Exception as exc:
            with store.db() as db:
                db.execute(
                    "UPDATE scheduled_drafts SET state='failed',error=? WHERE id=?",
                    (type(exc).__name__, plan["id"]),
                )
            logging.error("Scheduled draft failed: %s", type(exc).__name__)
            return "failed"


def main() -> None:
    """Read the local nonsecret plan; secrets remain in the normal settings loader."""
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    plan = json.loads(Path("config/scheduled-draft.json").read_text())
    result = tick(settings, Store(settings.storage_dir), plan, datetime.now(timezone.utc))
    if result not in {"waiting", "already_claimed"}:
        logging.info("Scheduled draft result: %s", result)


if __name__ == "__main__":
    main()
