"""Slack app utilities for the Geo Insulation blog agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings
    from .store import Store
import json
import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .slack_guard import safe_client


def keyword_summary(topic: dict[str, Any]) -> str:
    """Show supplied supporting terms when a Basecamp brief has no secondary phrase."""
    primary = f"Main keyword: {topic.get('keyword') or 'See draft'}"
    secondary = topic.get("secondary_keyword")
    supporting = topic.get("supporting_keywords") or []
    if secondary:
        return primary + f"\nSecondary keyword: {secondary}"
    if supporting:
        return primary + "\nSupporting keywords: " + ", ".join(supporting)
    return primary + "\nSupporting keywords: None supplied"


def topic_message(topic: dict[str, Any]) -> str:
    """Summarize the selected keyword brief without flooding the Slack thread."""
    text = (
        f"Geo Insulation Blog Agent picked a topic: {topic.get('topic', 'Geo Insulation blog')}\n"
        f"{keyword_summary(topic)}\n"
        f"Target: about {topic.get('target_words', 1400)} words.\n"
        "A short summary, website preview and review buttons will be in this thread."
    )
    return text


def completed_topic_message(draft: dict[str, Any]) -> str:
    """Replace the initial announcement with the finished summary and both review links."""
    return (
        draft["front_matter"]["title"]
        + "\n"
        + draft["front_matter"]["description"]
        + "\n\n"
        + keyword_summary(draft.get("topic", {}))
        + f"\nLength: {draft['report']['word_count']} words\n\n"
        + f"<{draft['preview_url']}|Open Vercel blog preview> • <{draft['pr_url']}|Open GitHub draft>"
        + "\n\nThe review card and approval buttons are in this thread."
    )


def start_thread(
    settings: Settings,
    store: Store,
    draft_id: str,
    topic: dict[str, Any],
    client: Any = None,
) -> Any:
    """The only channel-level message for a blog; all later work uses its thread."""
    client = safe_client(settings, client)
    if client.auth_test()["team_id"] != settings.slack_team_id:
        raise ValueError("Slack token belongs to a different workspace")
    row = store.get(draft_id)
    if not row:
        raise ValueError("Unknown draft")
    if row.get("thread_ts"):
        if row["channel"] != settings.slack_channel_id:
            raise ValueError("Saved thread belongs to another channel")
        return row["thread_ts"]
    message = topic_message(topic)
    result = client.chat_postMessage(
        channel=settings.slack_channel_id,
        text=message,
        unfurl_links=False,
        unfurl_media=False,
    )
    store.set_thread(draft_id, settings.slack_channel_id, result["ts"])
    return result["ts"]


def deliver(settings: Settings, store: Store, draft_id: str, client: Any = None) -> Any:
    """Claim delivery before sending the compact exact-version review card."""
    settings.require("slack_bot_token", "slack_approver_ids")
    client = safe_client(settings, client)
    if client.auth_test()["team_id"] != settings.slack_team_id:
        raise ValueError("Slack token belongs to a different workspace")
    row = store.get(draft_id)
    if row and row.get("thread_ts") and row["channel"] != settings.slack_channel_id:
        raise ValueError("Saved thread belongs to another channel")
    if row and row.get("payload"):
        candidate = json.loads(row["payload"])
        if candidate.get("nightly_run") and (
            candidate.get("google_verified") is not True or not candidate.get("preview_url")
        ):
            raise ValueError("Nightly review requires verified competitors and a website preview")
    if row and row["status"] == "ready" and settings.basecamp_draft_ready_enabled:
        from .basecamp_review import draft_ready

        draft_ready(settings, store, draft_id)
    if not row or not store.claim_delivery(draft_id, settings.slack_channel_id):
        raise ValueError("Draft is not ready or delivery already started")
    draft = json.loads(row["payload"])
    thread_ts = row.get("thread_ts") or start_thread(
        settings, store, draft_id, draft.get("topic", {}), client
    )
    blocks = review_blocks(draft, draft_id, settings=settings)
    if draft.get("nightly_run"):
        mentions = " ".join(f"<@{user}>" for user in sorted(settings.approvers))
        blocks.insert(
            1,
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": mentions + " Your blog and website preview are ready for review.",
                },
            },
        )
    result = client.chat_postMessage(
        channel=settings.slack_channel_id,
        text="Blog draft ready: " + draft["front_matter"]["title"],
        blocks=blocks,
        unfurl_links=False,
        unfurl_media=False,
        thread_ts=thread_ts,
        reply_broadcast=False,
    )
    store.delivered(draft_id, result["ts"])
    if draft.get("preview_url") and draft.get("pr_url"):
        try:
            client.chat_update(
                channel=settings.slack_channel_id,
                ts=thread_ts,
                text=completed_topic_message(draft),
            )
        except Exception as exc:
            # The review card already exists. Never resend it because a cosmetic
            # parent update failed; record a sanitized warning for inspection.
            logging.getLogger(__name__).warning(
                "Review parent update failed: %s", type(exc).__name__
            )
    return result["ts"]


def low_queue_notice(
    settings: Settings,
    store: Store,
    draft_id: str,
    topic: dict[str, Any],
    client: Any = None,
) -> Any:
    """Send at most one low-queue notice inside this draft thread."""
    remaining = topic.get("queue_remaining_after_selection")
    if remaining is None or remaining > settings.queue_low_threshold:
        return None
    row = store.get(draft_id)
    if not row or not row.get("thread_ts") or row["channel"] != settings.slack_channel_id:
        raise ValueError("A low-queue notice needs the saved blog thread")
    client = safe_client(settings, client)
    if client.auth_test()["team_id"] != settings.slack_team_id:
        raise ValueError("Slack token belongs to a different workspace")
    with store.db() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS queue_notices (notice_key TEXT PRIMARY KEY, message_ts TEXT)"
        )
        key = "queue-low:" + draft_id
        if not db.execute(
            "INSERT OR IGNORE INTO queue_notices(notice_key) VALUES(?)", (key,)
        ).rowcount:
            return None
    mention = " ".join(f"<@{user}>" for user in sorted(settings.approvers))
    message = (
        f"{mention} After this topic, {remaining} unused blog {'topic remains' if remaining == 1 else 'topics remain'}. "
        + (
            "Please review the Basecamp Content Sprint for future blog tasks."
            if settings.blog_queue_source == "basecamp"
            else "Please add more primary and supporting keywords to Blog Posts for future draft requests."
        )
    )
    result = client.chat_postMessage(
        channel=row["channel"],
        thread_ts=row["thread_ts"],
        text=message.strip(),
        unfurl_links=False,
        unfurl_media=False,
        reply_broadcast=False,
    )
    with store.db() as db:
        db.execute(
            "UPDATE queue_notices SET message_ts=? WHERE notice_key=?",
            (result["ts"], key),
        )
    return result["ts"]


def next_draft_schedule(
    draft_id: str,
    *,
    timezone: Any = "America/Los_Angeles",
    hour: int = 21,
    now: Any = None,
) -> str:
    """Format a configured future schedule without claiming the scheduler is running."""
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(timezone)
    current = (now or datetime.now(zone)).astimezone(zone)
    next_run = current.replace(hour=hour, minute=0, second=0, microsecond=0)
    if next_run <= current:
        next_run += timedelta(days=1)
    # Today's reservation is already consumed, even if delivered before 9 PM.
    if draft_id.startswith("nightly-"):
        try:
            consumed_day = date.fromisoformat(draft_id.removeprefix("nightly-"))
            if next_run.date() <= consumed_day:
                next_run = datetime.combine(consumed_day + timedelta(days=1), next_run.time(), zone)
        except ValueError:
            pass
    label = "Pacific" if timezone == "America/Los_Angeles" else (next_run.tzname() or timezone)
    return (
        "I’m scheduled to start your next blog on "
        + next_run.strftime("%A, %B %-d at %-I %p")
        + " "
        + label
        + "."
    )


def review_blocks(draft: dict[str, Any], draft_id: str, *, settings: Settings | None = None) -> Any:
    """Do not relink the uploaded file: Slack would preview it a second time."""
    topic = draft.get("topic", {})
    details = f"{keyword_summary(topic)}\nLength: {draft['report']['word_count']} words"
    if topic.get("target_words"):
        details += f" (target: about {topic['target_words']})"
    if draft.get("media_status") != "ready":
        details += "\nPhotos are pending; this is a text draft for review."
    if (draft.get("media_provenance") or {}).get("origin") == "generated":
        details += "\nImage: generated illustration (no matching approved project photo)"
    elif draft.get("media_provenance"):
        details += f"\nPhotos: {1 + len(draft.get('media_gallery', []))} from the Geo library"
    if draft.get("preview_url"):
        details += "\nThe website preview is ready for your review."
    if draft.get("google_verified") is False:
        details += "\nCompetitor comparison unavailable: Google blocked the browser check. No competitor rankings or scores are claimed."
    links = "Website preview is not available yet. The saved draft is awaiting preview setup."
    if draft.get("preview_url"):
        links = f"<{draft['preview_url']}|Open website preview>"
    if draft.get("pr_url"):
        links += f" • <{draft['pr_url']}|Review on GitHub>"
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": draft["front_matter"]["title"][:150],
            },
        },
        {
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": draft["front_matter"]["description"],
            },
        },
        {"type": "section", "text": {"type": "plain_text", "text": details[:2900]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{links}"}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "plain_text",
                    "text": (
                        "Approve publishes this version to geo-insulation.com after final checks. I’ll confirm the live link here."
                        if settings and settings.publishing_enabled
                        else "Approve saves your decision. The blog will not be published automatically."
                    ),
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "blog_approve",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "value": draft_id,
                },
                {
                    "type": "button",
                    "action_id": "blog_reject",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "value": draft_id,
                },
            ],
        },
    ]
    quality = draft.get("production_lighthouse")
    if quality and quality.get("environment") in {
        "local production build",
        "GitHub production build",
    }:
        scores = quality["scores"]
        label = (
            f"Production-build Lighthouse ({'GitHub' if quality.get('environment') == 'GitHub production build' else 'local'}, median of 3): Performance {scores['performance']}, "
            f"Accessibility {scores['accessibility']}, Best Practices {scores['best-practices']}, "
            f"SEO {scores['seo']}. Live scores are checked after publication."
        )
        blocks.insert(-1, {"type": "context", "elements": [{"type": "plain_text", "text": label}]})
    if draft.get("nightly_run") and settings and settings.daily_enabled:
        schedule = next_draft_schedule(
            draft_id,
            timezone=settings.timezone if settings else "America/Los_Angeles",
            hour=settings.daily_hour if settings else 21,
        )
        blocks.insert(
            -1,
            {"type": "context", "elements": [{"type": "plain_text", "text": schedule}]},
        )
    return blocks


def serve(settings: Settings, store: Store, daily_job: Any = None) -> None:
    """Hold a single-listener lock and handle authorized mentions and review buttons."""
    if not settings.slack_listener_enabled:
        raise ValueError(
            "Slack listener is disabled on this development Mac; the live agent runs on the shared host"
        )
    settings.require("slack_bot_token", "slack_app_token", "slack_approver_ids")
    from .slack_guard import require_test_channel

    require_test_channel(settings, settings.slack_channel_id)
    # A second Socket Mode consumer can steal events from the active worker.
    import fcntl

    listener_lock = (store.root / "listener.lock").open("a")
    try:
        fcntl.flock(listener_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        listener_lock.close()
        raise RuntimeError("A Geo listener is already running for this storage directory") from None
    app = App(token=settings.slack_bot_token.get_secret_value())
    identity = app.client.auth_test()
    if identity["team_id"] != settings.slack_team_id:
        raise ValueError("Slack token belongs to a different workspace")

    def review(ack: Any, body: dict[str, Any], client: Any) -> None:
        """Acknowledge a button and accept only a matching reviewer, channel and version."""
        ack()
        client = safe_client(settings, client)
        user = body["user"]["id"]
        channel = body["channel"]["id"]
        if (
            body.get("team", {}).get("id") != settings.slack_team_id
            or channel != settings.slack_channel_id
        ):
            return
        action = body["actions"][0]
        decision = "approved" if action["action_id"] == "blog_approve" else "rejected"
        changed = store.decide(
            action["value"],
            decision=decision,
            user=user,
            channel=channel,
            message_ts=body["message"]["ts"],
            allowed_users=settings.approvers,
            publish=settings.publishing_enabled,
        )
        if not changed:
            client.chat_postEphemeral(
                channel=channel,
                user=user,
                thread_ts=body["message"].get("thread_ts", body["message"]["ts"]),
                text="This review was not changed. You must be a configured reviewer and the draft must still be awaiting review.",
            )
            return
        text = f"Blog {decision} by <@{user}>."
        if decision == "approved":
            text += (
                " I’ll publish this version and confirm the live link here."
                if settings.publishing_enabled
                else " Saved as ready for publishing; website publishing is not connected."
            )
        blocks = [b for b in body["message"].get("blocks", []) if b.get("type") != "actions"]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
        client.chat_update(channel=channel, ts=body["message"]["ts"], text=text, blocks=blocks)

    from .conversation import handle

    def converse(event: dict[str, Any], client: Any) -> None:
        """Dispatch explicit draft commands before handling read-only thread questions."""
        from .production_review import handle_comment
        from .requests import maybe_draft

        if handle_comment(settings, event, client):
            return
        client = safe_client(settings, client)
        if not maybe_draft(settings, store, event, client, identity["user_id"]):
            handle(settings, store, event, client, identity["user_id"])

    app.event("message")(converse)
    app.event("app_mention")(converse)

    def production_review(ack: Any, body: dict[str, Any], client: Any) -> None:
        """Acknowledge the separately authorized production review card."""
        ack()
        from .production_review import handle_review

        handle_review(settings, body, client)

    app.action("geo_production_approve")(production_review)
    app.action("geo_production_reject")(production_review)
    app.action("blog_approve")(review)
    app.action("blog_reject")(review)
    scheduler = None
    if settings.daily_enabled:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(timezone=settings.timezone)
        scheduler.add_job(
            daily_job,
            "cron",
            hour=settings.daily_hour,
            minute=0,
            id="daily_blog",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        scheduler.start()
    import threading

    from .production_publish import start_worker

    publication_stop = threading.Event()
    start_worker(settings, publication_stop)
    try:
        SocketModeHandler(app, settings.slack_app_token.get_secret_value()).start()
    finally:
        publication_stop.set()
        listener_lock.close()
        if scheduler:
            scheduler.shutdown(wait=False)


def queue_notice(
    settings: Settings,
    store: Store,
    day: str,
    kind: Any,
    message: str,
    client: Any = None,
) -> Any:
    """One standalone queue status per day/kind when no blog thread can be started."""
    client = safe_client(settings, client)
    if client.auth_test()["team_id"] != settings.slack_team_id:
        raise ValueError("Slack token belongs to a different workspace")
    with store.db() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS queue_notices (notice_key TEXT PRIMARY KEY, message_ts TEXT)"
        )
        key = day + ":" + kind
        if not db.execute(
            "INSERT OR IGNORE INTO queue_notices(notice_key) VALUES(?)", (key,)
        ).rowcount:
            return None
    # An ambiguous timeout stays reserved; do not spam or retry silently.
    result = client.chat_postMessage(
        channel=settings.slack_channel_id, text=message, unfurl_links=False
    )
    with store.db() as db:
        db.execute(
            "UPDATE queue_notices SET message_ts=? WHERE notice_key=?",
            (result["ts"], key),
        )
    return result["ts"]
