"""Draft, delivery, and listener commands."""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .settings import Settings
from .slack_guard import safe_client
from .store import Store
from .topics import select_topic


def run_daily(
    settings: Settings,
    store: Store,
    *,
    send: bool = False,
    topic: dict[str, Any] | None = None,
    practice: bool = False,
    parent_thread: Any = None,
    request_key: Any = None,
) -> Any:
    """Reserve a draft, retain its evidence, and optionally deliver it in the saved thread."""
    from .content import Writer

    settings.require("anthropic_api_key")
    if send:
        settings.require("slack_bot_token", "slack_approver_ids")
    day = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    try:
        topic = topic or select_topic(settings, store.used_topics())
    except Exception as exc:
        if send:
            from .slack_app import queue_notice
            from .topics import HeldKeywordQueue, IncompleteKeywordQueue

            message = (
                "Your remaining blog topics are marked Retarget needed. Mark a topic Proposed or Ready when it is ready to write."
                if isinstance(exc, HeldKeywordQueue)
                else "I found unwritten blog rows, but they need both a primary keyword and a supporting keyword before I can start."
                if isinstance(exc, IncompleteKeywordQueue)
                else "I couldn’t read or validate the blog keyword queue. This is an access, connection or data error; I have not confirmed that keywords are exhausted."
            )
            queue_notice(settings, store, day, "queue-error", message)
        raise
    if not topic:
        message = (
            "No unused Basecamp blog tasks are ready to draft. Existing drafts and completed tasks are skipped; please review the Content Sprint queue."
            if settings.blog_queue_source == "basecamp"
            else "I’m completely out of keywords. Every eligible blog topic has already been used, or there are no unwritten topics left. Add a new primary and supporting keyword to Blog Posts."
        )
        logging.info(message)
        if send:
            from .slack_app import queue_notice

            mention = " ".join(f"<@{user}>" for user in sorted(settings.approvers))
            queue_notice(settings, store, day, "queue-empty", (mention + " " + message).strip())
        return None
    if settings.website_preview_enabled:
        from .site_preview import preflight

        # Do not reserve a topic or spend on Claude if main cannot build its preview.
        preflight(settings)
    draft_id = uuid.uuid4().hex[:16]
    from .claude_usage import workflow_estimate, workflow_estimate_text

    topic = dict(
        topic,
        claude_cost_estimate=workflow_estimate(settings.writer_model, include_visuals=False),
    )
    print(workflow_estimate_text(topic["claude_cost_estimate"]), flush=True)
    run_key = (
        "request:" + request_key
        if request_key
        else ("practice:" + draft_id if practice else "daily:" + day)
    )
    if not store.reserve(draft_id, run_key, topic["id"]):
        logging.info("Today's run already exists; no duplicate generated.")
        return None
    folder = settings.storage_dir / draft_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "topic.json").write_text(json.dumps(topic, indent=2))
    try:
        if parent_thread:
            store.set_thread(draft_id, settings.slack_channel_id, parent_thread)
        if send:
            from .slack_app import start_thread

            start_thread(settings, store, draft_id, topic)
            from .slack_app import low_queue_notice

            low_queue_notice(settings, store, draft_id, topic)
            from .competitors import update_thread

            if settings.competitors_enabled:
                update_thread(settings, store, draft_id, topic)
        if not send and settings.competitors_enabled:
            from .competitors import collect

            collect(settings, topic, settings.storage_dir / draft_id / "competitors")
        writer = Writer(settings)
        draft = writer.draft(topic, day, settings.storage_dir / draft_id)
        from .basecamp_queue import validate_basecamp_draft

        validate_basecamp_draft(draft)
        from .media_catalog import attach_media

        draft = attach_media(settings, draft)
        # Never publish without an image: generate a labeled illustration if no
        # approved project photo matched (owner rule, September 28, 2026).
        from .generated_hero import ensure_image

        draft = ensure_image(settings, writer, draft)
        if settings.website_preview_enabled:
            from .site_preview import prepare

            draft = prepare(settings, draft, draft_id)
        (settings.storage_dir / draft_id / "payload.json").write_text(json.dumps(draft, indent=2))
        store.save(draft_id, draft)
    except Exception as exc:
        store.fail(draft_id, type(exc).__name__)
        row = store.required(draft_id)
        if send and row.get("thread_ts"):
            try:
                safe_client(settings).chat_postMessage(
                    channel=row["channel"],
                    thread_ts=row["thread_ts"],
                    reply_broadcast=False,
                    text="The draft stopped before it was ready for review. No approval card was sent. This run needs inspection before retrying.",
                )
            except Exception as notice_error:
                logging.error(
                    "Could not post draft failure notice: %s",
                    type(notice_error).__name__,
                )
        raise
    if send:
        from .slack_app import deliver

        deliver(settings, store, draft_id)
    print("Draft saved:", draft_id)
    return draft_id


def main() -> None:
    """Parse explicit command options and run the requested operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Show configuration presence without exposing secrets")
    sub.add_parser("basecamp-queue", help="Read the next Basecamp task without drafting or posting")
    review = sub.add_parser(
        "basecamp-review", help="Deliver or reconcile a saved draft-ready Basecamp update"
    )
    review.add_argument("draft_id")
    daily = sub.add_parser("daily", help="Research and draft once per configured local day")
    daily.add_argument(
        "--send", action="store_true", help="Send finished draft to the test channel"
    )
    practice = sub.add_parser(
        "practice", help="Draft from a local brief without reading or writing Airtable"
    )
    practice.add_argument(
        "--send",
        action="store_true",
        help="Announce once, then deliver in the same Slack thread",
    )
    practice.add_argument(
        "brief", help="JSON file with topic, keyword, secondary_keyword, and angle"
    )
    export = sub.add_parser(
        "export",
        help="Export a ready article into an isolated Geo Insulation website checkout",
    )
    export.add_argument("draft_id")
    export.add_argument("checkout")
    send = sub.add_parser("send", help="Send a previously generated draft")
    send.add_argument("draft_id")
    competitors = sub.add_parser(
        "competitors",
        help="Measure competitors and update the saved opening Slack message",
    )
    competitors.add_argument("draft_id")
    competitors.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch a fresh Google comparison and update the same opening message",
    )
    show = sub.add_parser("show", help="Inspect saved draft status")
    show.add_argument("draft_id")
    publish = sub.add_parser(
        "publish",
        help="Publish queued approvals; optionally queue a previously approved draft",
    )
    publish.add_argument(
        "--retry", help="Resume this failed publication without making another article"
    )
    publish.add_argument("--draft", help="Explicitly queue this already approved draft")
    sub.add_parser(
        "revisions",
        help="Process the next queued revision with the shared nightly build lock",
    )
    weekly = sub.add_parser(
        "weekly",
        help="Thursday run: share the staged (or a new) blog in the production channel for approval",
    )
    weekly.add_argument(
        "--test",
        action="store_true",
        help="Write next week's blog and review it only in the test channel",
    )
    sub.add_parser("serve", help="Listen for approval buttons; optional daily scheduler")
    nightly = sub.add_parser(
        "nightly",
        help="Run browser research, illustrated article, website preview and Slack review",
    )
    nightly.add_argument(
        "--retry",
        action="store_true",
        help="Explicitly retry today's failed run in the same thread",
    )
    nightly.add_argument(
        "--draft", help="With --retry, resume this saved nightly ID even after midnight"
    )
    args = parser.parse_args()
    settings = Settings()
    if args.command == "doctor":
        from .doctor import report_readiness

        if not report_readiness(settings):
            raise SystemExit(1)
        return
    store = Store(settings.storage_dir)
    if args.command == "basecamp-queue":
        from .basecamp_queue import select_basecamp_topic

        topic = select_basecamp_topic(settings, store.used_topics())
        print(
            json.dumps(
                {
                    k: topic[k]
                    for k in (
                        "id",
                        "topic",
                        "keyword",
                        "planned_url",
                        "queue_remaining_after_selection",
                    )
                }
                if topic
                else {"next": None},
                indent=2,
            )
        )
    elif args.command == "basecamp-review":
        from .basecamp_review import draft_ready

        print(draft_ready(settings, store, args.draft_id))
    elif args.command == "publish":
        from .publishing import enqueue_existing, retry_failed, run_next

        if args.retry:
            retry_failed(settings, store, args.retry)
        if args.draft:
            enqueue_existing(settings, store, args.draft)
        run_next(settings, store)
    elif args.command == "revisions":
        from .revisions import run_next as run_revision

        run_revision(settings, store)
    elif args.command == "nightly":
        from .nightly import run_nightly

        run_nightly(settings, store, retry=args.retry, draft_id=args.draft)
    elif args.command == "weekly":
        from .weekly import run_weekly_notifying, stage_test

        print((stage_test if args.test else run_weekly_notifying)(settings, store))
    elif args.command == "daily":
        run_daily(settings, store, send=args.send)
    elif args.command == "practice":
        from pathlib import Path

        topic = json.loads(Path(args.brief).read_text())
        topic.setdefault("id", "practice:" + topic["keyword"])
        run_daily(settings, store, topic=topic, practice=True, send=args.send)
    elif args.command == "competitors":
        from .competitors import update_thread

        update_thread(settings, store, args.draft_id, refresh=args.refresh)
    elif args.command == "send":
        row = store.get(args.draft_id)
        if row and row["status"] == "ready":
            from .slack_app import start_thread

            topic = json.loads(row["payload"])["topic"]
            start_thread(settings, store, args.draft_id, topic)
            from .competitors import update_thread

            update_thread(settings, store, args.draft_id, topic)
        from .slack_app import deliver

        deliver(settings, store, args.draft_id)
    elif args.command == "export":
        from .website import export_preview

        print(export_preview(store, args.draft_id, args.checkout))
    elif args.command == "show":
        print(json.dumps(store.get(args.draft_id), indent=2))
    elif args.command == "serve":
        from .nightly import run_nightly
        from .slack_app import serve

        serve(settings, store, daily_job=lambda: run_nightly(settings, store))
