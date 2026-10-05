"""Mention-triggered drafts with durable event deduplication and one local worker.

Only configured reviewers can spend API credits. A process crash leaves a visible
request record for inspection; it never silently repeats paid work or Slack sends.
"""

from __future__ import annotations

import fcntl
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from .settings import Settings
from .slack_guard import ChannelClient, require_test_channel
from .store import Store

WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="geo-drafts")


def initialize(store: Store) -> None:
    """Create the durable inbox independently of the article state machine."""
    with store.db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS draft_requests (
            event_key TEXT PRIMARY KEY, channel TEXT NOT NULL, thread_ts TEXT NOT NULL,
            requested_by TEXT NOT NULL, selector TEXT NOT NULL, state TEXT NOT NULL,
            draft_id TEXT, created TEXT NOT NULL, error TEXT)""")


def maybe_draft(
    settings: Settings,
    store: Store,
    event: dict[str, Any],
    client: ChannelClient,
    bot_user_id: str,
) -> bool:
    """Recognize explicit drafting commands; leave other questions to chat."""
    if (
        event.get("bot_id")
        or event.get("subtype")
        or event.get("user") not in settings.approvers
        or event.get("channel") != settings.slack_channel_id
    ):
        return False
    mention = f"<@{bot_user_id}>"
    text = event.get("text", "").strip()
    if mention not in text or not event.get("ts"):
        return False
    command = text.replace(mention, "").strip()
    match = re.fullmatch(
        r"(?:draft|write|create)(?:\s+(?:a\s+)?blog)?\s+(next|rec[A-Za-z0-9]+|basecamp:[0-9]+)",
        command,
        re.I,
    )
    if not match:
        return False
    require_test_channel(settings, event["channel"])
    initialize(store)
    key = event["channel"] + ":" + event["ts"]
    thread = event.get("thread_ts") or event["ts"]
    with store.db() as db:
        inserted = db.execute(
            "INSERT OR IGNORE INTO draft_requests VALUES(?,?,?,?,?,'queued',NULL,?,NULL)",
            (
                key,
                event["channel"],
                thread,
                event["user"],
                match[1],
                datetime.now(timezone.utc).isoformat(),
            ),
        ).rowcount
    if inserted:
        WORKER.submit(process_request, settings, store, key, client)
    return True


def process_request(settings: Settings, store: Store, key: str, client: ChannelClient) -> None:
    """Claim one request and serialize topic selection across local processes."""
    from .cli import run_daily
    from .topics import select_topic

    with (store.root / "draft-request.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with store.db() as db:
            row = db.execute("SELECT * FROM draft_requests WHERE event_key=?", (key,)).fetchone()
            if not row or row["state"] != "queued":
                return
            db.execute("UPDATE draft_requests SET state='working' WHERE event_key=?", (key,))
        try:
            topic = select_topic(
                settings,
                store.used_topics(),
                record_id=None if row["selector"].lower() == "next" else row["selector"],
            )
            if topic is None:
                raise ValueError("No unused matching keyword group is available")
            client.chat_postMessage(
                channel=row["channel"],
                thread_ts=row["thread_ts"],
                text="I’m drafting a blog for “"
                + topic["keyword"]
                + "”. I’ll put the article and review buttons in this thread.",
            )
            draft_id = run_daily(
                settings,
                store,
                send=True,
                topic=topic,
                practice=True,
                parent_thread=row["thread_ts"],
                request_key=key,
            )
            with store.db() as db:
                db.execute(
                    "UPDATE draft_requests SET state='complete',draft_id=? WHERE event_key=?",
                    (draft_id, key),
                )
        except Exception as exc:
            logging.error("Draft request failed: %s", type(exc).__name__)
            with store.db() as db:
                db.execute(
                    "UPDATE draft_requests SET state='failed',error=? WHERE event_key=?",
                    (type(exc).__name__, key),
                )
            # A failed request is retained, rather than automatically redelivered.
            try:
                client.chat_postMessage(
                    channel=row["channel"],
                    thread_ts=row["thread_ts"],
                    text="This draft request stopped. Nothing was published. The saved run needs inspection before retrying.",
                )
            except Exception:
                logging.error("Could not deliver request failure notice")
