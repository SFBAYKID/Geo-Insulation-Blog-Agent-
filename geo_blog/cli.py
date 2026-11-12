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
    excluded_topics: set[str] | None = None,
) -> Any:
    """Serialize research drafting across schedule, command, and request processes."""
    import fcntl

    options = dict(
        send=send,
        topic=topic,
        practice=practice,
        parent_thread=parent_thread,
        request_key=request_key,
        excluded_topics=excluded_topics,
    )
    if settings.blog_queue_source != "research":
        return _run_daily(settings, store, **options)
    with (settings.storage_dir / "research-draft.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_daily(settings, store, **options)


def _run_daily(
    settings: Settings,
    store: Store,
    *,
    send: bool = False,
    topic: dict[str, Any] | None = None,
    practice: bool = False,
    parent_thread: Any = None,
    request_key: Any = None,
    excluded_topics: set[str] | None = None,
) -> Any:
    """Reserve a draft, retain its evidence, and optionally deliver it in the saved thread."""
    from .content import Writer

    settings.require("openai_api_key")
    if send:
        settings.require("slack_bot_token", "slack_approver_ids")
    day = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    from .writer_feedback import topic_blockers

    # Research rows that cannot pass are Held inside select_research. Basecamp tasks
    # cannot be held from here, so skip them for this run and tell Chase; otherwise the
    # same impossible task would stop every weekly run.
    skipped: dict[str, str] = {}
    try:
        while not topic:
            used = store.used_topics() | set(skipped) | (excluded_topics or set())
            if settings.blog_queue_source == "research":
                from .research_queue import select_research

                candidate = select_research(settings, used, hold_duplicates=True)
            else:
                candidate = select_topic(settings, used)
            if not candidate:
                break
            blockers = topic_blockers(str(candidate.get("keyword", "")))
            if not blockers:
                topic = candidate
                break
            skipped[str(candidate["id"])] = " ".join(blockers)
            if len(skipped) >= 25:
                raise ValueError(
                    "Too many queued topics fail the website limits; inspect the queue"
                )
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
    if skipped:
        from .weekly import private_alert

        private_alert(
            settings,
            "Skipped queued blog topics that cannot pass the website limits:\n"
            + "\n".join(f"• {key}: {why}" for key, why in skipped.items())
            + "\nShorten their primary keywords so they can be drafted.",
        )
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
    # A topic passed in directly (a request or brief) is never silently swapped.
    if blockers := topic_blockers(str(topic.get("keyword", ""))):
        raise ValueError("Topic cannot pass the website limits: " + " ".join(blockers))
    if settings.website_preview_enabled:
        from .site_preview import preflight

        # Do not reserve a topic or spend on generation if main cannot build its preview.
        preflight(settings)
    draft_id = uuid.uuid4().hex[:16]
    from .model_usage import workflow_estimate, workflow_estimate_text

    topic = dict(
        topic,
        model_cost_estimate=workflow_estimate(settings.writer_model, include_visuals=False),
    )
    print(workflow_estimate_text(topic["model_cost_estimate"]), flush=True)
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
        from .research_lifecycle import claim, transition

        claim(settings, topic, draft_id)
        (folder / "topic.json").write_text(json.dumps(topic, indent=2))
        transition(settings, topic, "Drafting")
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
        from .writer_feedback import DraftQualityError

        if isinstance(exc, DraftQualityError):
            exc.topic_id = str(topic["id"])
        if topic.get("research_claimed_at"):
            try:
                from .quality_failures import release_failed_claim

                if release_failed_claim(settings, store, topic, draft_id, exc) == "Held":
                    # Read by weekly.notify_failure so the alert says the queue moved on.
                    exc.topic_held = (  # type: ignore[attr-defined]
                        f'The topic "{topic.get("keyword", "")}" failed repeatedly and is now '
                        "Held in Airtable; the next attempt will take the next Proposed topic."
                    )
            except Exception as recovery_error:
                if isinstance(exc, DraftQualityError):
                    exc.topic_id = ""  # Do not continue after an ambiguous lifecycle write.
                logging.error("Research reset needs inspection: %s", type(recovery_error).__name__)
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
    from .research_lifecycle import transition

    transition(settings, topic, "Review")
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
    local_draft = sub.add_parser(
        "local-draft", help="Generate a local draft and branded preview; never send or publish"
    )
    local_draft.add_argument("brief")
    local_draft.add_argument("--output", default="previews")
    local_preview = sub.add_parser(
        "local-preview", help="Render a saved approved payload locally without paid calls"
    )
    local_preview.add_argument("payload")
    local_preview.add_argument("--output", default="previews")
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
    parser.add_argument("--env-file")
    args = parser.parse_args()
    settings_options: dict[str, Any] = {"_env_file": args.env_file} if args.env_file else {}
    settings = Settings(**settings_options)
    if args.env_file and settings.github_token.get_secret_value():
        import os

        os.environ["GH_TOKEN"] = settings.github_token.get_secret_value()
    if args.command == "doctor":
        from .doctor import report_readiness

        if not report_readiness(settings):
            raise SystemExit(1)
        return
    if args.command in {"local-draft", "local-preview"}:
        from pathlib import Path

        from .local_preview import export_preview as export_local
        from .local_workflow import build_local

        path = (
            build_local(settings, Path(args.brief), Path(args.output))
            if args.command == "local-draft"
            else export_local(Path(args.payload), Path(args.output))
        )
        print("Local preview:", path.resolve())
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


if __name__ == "__main__":
    main()
