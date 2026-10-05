"""One daily browser-to-preview run, with durable stages and thread-only progress."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings
    from .store import Store
import fcntl
import json
import logging
import subprocess
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .competitors import pick_results, update_thread
from .content import Writer
from .preview_pipeline import deploy_preview
from .slack_app import deliver, low_queue_notice, queue_notice, start_thread
from .slack_guard import safe_client
from .topics import (
    HeldKeywordQueue,
    IncompleteKeywordQueue,
    secondary_keyword_from_brief,
    select_topic,
)
from .visuals import complete_visuals


def run_nightly(
    settings: Settings,
    store: Store,
    *,
    retry: bool = False,
    draft_id: str | None = None,
) -> Any:
    """Reject unattended runs until the actual website workflow is integrated."""
    raise RuntimeError("Unattended nightly runs are not enabled; use mention-triggered drafts")
    settings.require(
        "anthropic_api_key",
        "slack_bot_token",
        "slack_approver_ids",
        "airtable_token",
        "website_source",
        "website_base_commit",
    )
    if settings.google_search_mode == "serpapi":
        settings.require("serpapi_api_key")
    lock = (settings.storage_dir / "nightly.lock").open("a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        return _run(settings, store, retry=retry, draft_id=draft_id)
    finally:
        lock.close()


def _run(
    settings: Settings,
    store: Store,
    *,
    retry: bool = False,
    draft_id: str | None = None,
) -> Any:
    day = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    if draft_id is not None:
        if not retry or not draft_id.startswith("nightly-"):
            raise ValueError("An explicit nightly draft requires --retry and a saved nightly ID")
        saved_day = draft_id.removeprefix("nightly-")
        if date.fromisoformat(saved_day).isoformat() != saved_day or saved_day > day:
            raise ValueError("Invalid saved nightly date")
        saved = store.get(draft_id)
        if not saved or saved["run_key"] != "daily:" + saved_day:
            raise ValueError("Explicit retry requires an existing daily reservation")
        day = saved_day
    else:
        draft_id = "nightly-" + day
    existing = store.get(draft_id)
    if existing and not (retry and existing["status"] in {"failed", "writing", "ready"}):
        # Never duplicate a finished run or retry ambiguous delivery automatically.
        logging.info("Nightly reservation already exists: %s", existing["status"])
        return draft_id
    client = safe_client(settings)
    if client.auth_test()["team_id"] != settings.slack_team_id:
        raise ValueError("Wrong Slack workspace")
    mentions = " ".join(f"<@{u}>" for u in sorted(settings.approvers))
    try:
        topic = (
            json.loads((settings.storage_dir / draft_id / "topic.json").read_text())
            if existing
            else select_topic(settings, store.used_topics())
        )
        if existing and not existing["payload"] and topic.get("supporting_keywords_brief"):
            secondary = secondary_keyword_from_brief(topic["supporting_keywords_brief"])
            if not secondary:
                raise IncompleteKeywordQueue("Saved brief has no usable supporting keyword")
            topic["secondary_keyword"] = secondary
    except Exception as exc:
        message = (
            "Your remaining blog topics are marked “Retarget needed.” Mark a topic Proposed or Ready when it is ready to write."
            if isinstance(exc, HeldKeywordQueue)
            else "Some blog rows need both a primary and supporting keyword before I can use them."
            if isinstance(exc, IncompleteKeywordQueue)
            else "I couldn’t read the blog keyword list. I haven’t marked any topics as used."
        )
        queue_notice(
            settings,
            store,
            day,
            "queue-held" if isinstance(exc, HeldKeywordQueue) else "queue-error",
            mentions + " " + message,
            client,
        )
        raise
    if not topic:
        queue_notice(
            settings,
            store,
            day,
            "queue-empty",
            mentions
            + " I’m completely out of keywords. Please add primary and supporting keywords to Blog Posts.",
            client,
        )
        return None
    if existing:
        with store.db() as db:
            db.execute(
                "UPDATE drafts SET status=?,error=NULL WHERE id=? AND status IN ('failed','writing','ready')",
                ("ready" if existing["payload"] else "writing", draft_id),
            )
    elif not store.reserve(draft_id, "daily:" + day, topic["id"]):
        return None
    folder = settings.storage_dir / draft_id
    folder.mkdir(parents=True, exist_ok=True)
    if not (existing and existing["payload"]) and not (folder / "prepared-payload.json").exists():
        from .claude_usage import workflow_estimate

        topic["claude_cost_estimate"] = workflow_estimate(settings.writer_model)
        (folder / "cost-estimate.json").write_text(
            json.dumps(topic["claude_cost_estimate"], indent=2)
        )
    (folder / "topic.json").write_text(json.dumps(topic, indent=2))
    stage = "starting"

    def progress(text: str) -> None:
        """Send status only within the already bound draft thread."""
        row = store.required(draft_id)
        client.chat_postMessage(
            channel=row["channel"],
            thread_ts=row["thread_ts"],
            text=text,
            unfurl_links=False,
            unfurl_media=False,
            reply_broadcast=False,
        )

    try:
        start_thread(settings, store, draft_id, topic, client)
        low_queue_notice(settings, store, draft_id, topic, client)
        stage = "Google research"
        evidence = folder / "competitors"
        evidence.mkdir(exist_ok=True)
        # A fresh observation from browser setup can finish this same run. Old observations never qualify.
        try:
            saved = json.loads((evidence / "google-search.json").read_text())
            verified = len(pick_results(saved, topic["keyword"])) == 3
        except (OSError, ValueError, KeyError):
            verified = False
        try:
            if not verified:
                if settings.google_search_mode == "serpapi":
                    from .google_search import google_results

                    for attempt in range(3):
                        try:
                            raw = google_results(settings, topic["keyword"])
                            break
                        except Exception:
                            if attempt == 2:
                                raise
                            # Each attempt is an uncached scrape; give Google time to serve a full page.
                            time.sleep(5 * 2**attempt)
                    (evidence / "google-search.json").write_text(json.dumps(raw, indent=2))
                else:
                    with (folder / "browser.log").open("w") as log:
                        subprocess.run(
                            [
                                "node",
                                "tools/google_browser.mjs",
                                topic["keyword"],
                                str(evidence),
                            ],
                            check=True,
                            timeout=90,
                            stdout=log,
                            stderr=log,
                        )
            raw = json.loads((evidence / "google-search.json").read_text())
            verified = len(pick_results(raw, topic["keyword"])) == 3
            if not verified:
                raise ValueError("Three verified Google pages required")
        except Exception:
            verified = False
            raise
        update_thread(settings, store, draft_id, topic, client)
        stage = "writing and editorial review"
        writer = Writer(settings)
        if (folder / "prepared-payload.json").exists():
            draft = json.loads((folder / "prepared-payload.json").read_text())
            if draft["topic"]["id"] != topic["id"]:
                raise ValueError("Prepared draft belongs to another topic")
        elif store.required(draft_id)["payload"]:
            draft = json.loads(store.required(draft_id)["payload"])
        else:
            draft = writer.draft(topic, day, folder)
            stage = "illustrations"
            draft = complete_visuals(writer, draft, folder, day)
            (folder / "prepared-payload.json").write_text(json.dumps(draft, indent=2))
        draft["nightly_run"] = True
        draft["google_verified"] = verified
        if store.required(draft_id)["status"] == "writing":
            store.save(draft_id, draft)
        else:
            with store.db() as db:
                db.execute(
                    "UPDATE drafts SET payload=? WHERE id=? AND status='ready'",
                    (json.dumps(draft), draft_id),
                )
        (folder / "payload.json").write_text(json.dumps(draft, indent=2))
        stage = "website preview"
        deployment = deploy_preview(settings, store, draft_id)
        stage = "Slack delivery"
        deliver(settings, store, draft_id, client)
        (folder / "completed.json").write_text(
            json.dumps(
                {
                    "draft_id": draft_id,
                    "message_ts": store.required(draft_id)["message_ts"],
                    "google_verified": verified,
                    **deployment,
                },
                indent=2,
            )
        )
        return draft_id
    except Exception as exc:
        row = store.required(draft_id)
        # Preserve ambiguous sending, and preserve ready payloads for inspection.
        if row["status"] not in {"sending", "pending"}:
            store.fail(draft_id, type(exc).__name__ + " at " + stage)
        try:
            if row.get("thread_ts"):
                detail = (
                    "I couldn’t verify the competing pages. No draft is ready to approve yet; the competitor check needs another attempt."
                    if stage == "Google research"
                    else f"I couldn’t finish the {stage} step. This topic needs another attempt before it is ready to approve."
                )
                progress(mentions + " " + detail)
        except Exception:
            logging.exception("Could not send the nightly failure notice")
        raise
