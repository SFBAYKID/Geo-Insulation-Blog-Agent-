"""Weekly production review of one finished blog, Wednesdays at 9 AM Pacific.

Only a checked main-targeting PR is shared, with an exact-version approval card.
Playground approval never publishes. Unfinished production reviews block new runs.
"""

from __future__ import annotations

import fcntl
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import Settings
from .store import Store

CHASE = "U01DPJVURHU"
CLOSED_REVIEW_STATES = {"published", "rejected"}
REUSABLE_DRAFT_STATES = {"ready", "pending", "approved"}


def staged_path(settings: Settings) -> Path:
    """The test-reviewed draft waiting for the next Wednesday share."""
    return settings.storage_dir / "weekly-next.json"


def stage_test(settings: Settings, store: Store) -> str | None:
    """Write the next blog and deliver it only in the test channel for owner review."""
    path = staged_path(settings)
    if path.exists():
        staged = json.loads(path.read_text())
        row = store.get(staged["draft_id"])
        if row and row["status"] in REUSABLE_DRAFT_STATES:
            raise ValueError(f"Draft {staged['draft_id']} is already staged for Wednesday")
    from .cli import run_daily

    # the reviewer's Basecamp notice waits for the real production share.
    test_settings = settings.model_copy(update={"basecamp_draft_ready_enabled": False})
    draft_id = run_daily(test_settings, store, send=True)
    if draft_id:
        path.write_text(
            json.dumps({"draft_id": draft_id, "staged_at": datetime.now(timezone.utc).isoformat()})
        )
    return str(draft_id) if draft_id else None


def open_review_state(settings: Settings) -> str | None:
    """Return the state of an unfinished production review, if one exists."""
    path = settings.storage_dir / "production-review.json"
    if not path.exists():
        return None
    state = json.loads(path.read_text()).get("state")
    return None if state in CLOSED_REVIEW_STATES else str(state)


def run_weekly(settings: Settings, store: Store) -> str | None:
    """Wednesday entry point; single-run lock, never overlaps an open review."""
    settings.require(
        "production_delivery_enabled",
        "publishing_enabled",
        "slack_production_channel_id",
        "slack_approver_ids",
    )
    with (settings.storage_dir / "weekly.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.info("Weekly run already active")
            return None
        state = open_review_state(settings)
        if state:
            raise ValueError(f"Previous production review is still {state}; resolve it first")
        draft_id = None
        path = staged_path(settings)
        if path.exists():
            candidate = json.loads(path.read_text())["draft_id"]
            row = store.get(candidate)
            if row and row["status"] in REUSABLE_DRAFT_STATES:
                draft_id = candidate
        if not draft_id:
            from .cli import run_daily

            draft_id = run_daily(settings, store, send=False)
        if not draft_id:
            # Empty queue, or today's reservation already used by a failed attempt.
            raise ValueError(
                "No finished blog was produced: the Basecamp queue is empty or today's "
                "draft reservation was already used; check the service log"
            )
        share(settings, store, draft_id)
        return str(draft_id)


def archive_previous(settings: Settings) -> None:
    """Keep the finished review/plan beside its own draft before starting the next."""
    review_file = settings.storage_dir / "production-review.json"
    if not review_file.exists():
        return
    old = json.loads(review_file.read_text())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = settings.storage_dir / str(old.get("draft_id", "unknown"))
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("production-review.json", "publication-plan.json"):
        source = settings.storage_dir / name
        if source.exists():
            source.rename(folder / f"{name.removesuffix('.json')}-final-{stamp}.json")


def share(settings: Settings, store: Store, draft_id: str) -> dict[str, Any]:
    """Post the exact checked version to the production channel and arm publication for that commit."""
    from .production_publish import api
    from .production_review import review_blocks
    from .site_preview import REPOSITORY, command, require_ci_quality
    from .slack_guard import safe_client

    settings.require(
        "production_delivery_enabled",
        "publishing_enabled",
        "slack_production_channel_id",
        "slack_approver_ids",
    )
    folder = settings.storage_dir / draft_id
    receipt_path = folder / "weekly-share.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("state") == "complete":
            return dict(receipt)
        raise ValueError("A previous weekly share is unresolved; inspect Slack before retrying")
    row = store.required(draft_id)
    if row["status"] not in REUSABLE_DRAFT_STATES or not row.get("payload"):
        raise ValueError("Draft is not ready to share")
    draft = json.loads(row["payload"])
    commit = draft.get("preview_commit")
    if not (commit and draft.get("preview_url") and draft.get("pr_url")):
        raise ValueError("Weekly share requires a checked website preview")
    # Only finished illustrated articles may reach the client.
    if draft.get("media_status") != "ready" or not draft.get("media_provenance"):
        raise ValueError("Weekly share requires an verified image; this draft has none")
    checkout = (folder / "website").resolve()
    if command(["git", "rev-parse", "HEAD"], checkout) != commit or command(
        ["git", "status", "--porcelain"], checkout
    ):
        raise ValueError("Preview checkout changed after review")
    require_ci_quality(checkout, commit)
    pr_number = int(str(draft["pr_url"]).rstrip("/").rsplit("/", 1)[1])
    pr = api(checkout, f"repos/{REPOSITORY}/pulls/{pr_number}")
    if (
        pr["head"]["sha"] != commit
        or pr.get("state") != "open"
        or pr.get("base", {}).get("ref") != "main"
        or pr.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY
        or pr.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY
    ):
        raise ValueError("Website PR no longer matches the reviewed version")
    if pr.get("draft"):
        # The publisher merges only non-draft PRs; readiness is the owner's weekly consent.
        command(["gh", "pr", "ready", str(pr_number)], checkout)
    base_sha = api(checkout, f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"]
    if settings.basecamp_draft_ready_enabled:
        try:
            from .basecamp_review import draft_ready

            draft_ready(settings, store, draft_id)
        except Exception as exc:
            # the reviewer's Basecamp notice must not block the client's review; tell Chase privately.
            logging.error("Basecamp draft-ready notice needs inspection: %s", type(exc).__name__)
            private_alert(
                settings,
                f"The blog is going to the client, but the Basecamp comment for the reviewer failed "
                f"({type(exc).__name__}: {str(exc)[:200]}). Ask the blog agent to fix it.",
            )
    archive_previous(settings)
    receipt_path.write_text(json.dumps({"state": "sending", "preview_commit": commit}))
    client = safe_client(settings, production_share=True)
    title = draft["front_matter"]["title"]
    parent = client.chat_postMessage(
        channel=settings.slack_production_channel_id,
        text=(
            f"{' '.join(f'<@{user}>' for user in sorted(settings.approvers))} This week's blog is ready for review: *{title}*\n"
            "The preview and Approve button are in this thread. Approving publishes it to the "
            "website. To request changes, reply in the thread and mention me."
        ),
    )
    blocks = review_blocks(draft, commit, publishing=True)
    card = client.chat_postMessage(
        channel=settings.slack_production_channel_id,
        thread_ts=parent["ts"],
        text="Blog ready for review: " + title,
        blocks=blocks,
    )
    review = {
        "state": "pending",
        "draft_id": draft_id,
        "message_ts": card["ts"],
        "thread_ts": parent["ts"],
        "preview_commit": commit,
        "preview_url": draft["preview_url"],
        "blocks": blocks,
        "publication_armed": True,
        "comments": [],
        "revision_history": [],
    }
    plan = {
        "armed": True,
        "draft_id": draft_id,
        "head_sha": commit,
        "base_sha": base_sha,
        "pr_number": pr_number,
    }
    review_file = settings.storage_dir / "production-review.json"
    with review_file.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (settings.storage_dir / "publication-plan.json").write_text(json.dumps(plan, indent=2))
        review_file.write_text(json.dumps(review, indent=2))
    receipt = {"state": "complete", "thread_ts": parent["ts"], "message_ts": card["ts"], **plan}
    receipt_path.write_text(json.dumps(receipt, indent=2))
    staged = staged_path(settings)
    if staged.exists() and json.loads(staged.read_text()).get("draft_id") == draft_id:
        staged.unlink()
    return receipt


def notify_failure(settings: Settings, exc: BaseException) -> None:
    """Tell Chase in the test channel; failures never appear in the client's channel.

    Owner preference (September 28, 2026): a fixed run posted late beats a visible
    failure in production.
    """
    detail = (type(exc).__name__ + ": " + str(exc))[:300]
    private_alert(
        settings,
        "This week's blog did not go out. Nothing was posted in the client's channel.\n"
        f"Reason: {detail}\n"
        "Ask the blog agent to fix it and rerun this week's blog.",
    )


def private_alert(settings: Settings, text: str) -> None:
    """Tag Chase in the test channel; never raises, never touches production."""
    from .slack_guard import safe_client

    try:
        safe_client(settings).chat_postMessage(
            channel=settings.slack_test_channel_id, text=f"<@{CHASE}> {text}"
        )
    except Exception as exc:
        logging.error("Could not send private alert: %s", type(exc).__name__)


def run_weekly_notifying(settings: Settings, store: Store) -> str | None:
    """Timer entry point: any failure is reported privately, then re-raised for systemd."""
    try:
        return run_weekly(settings, store)
    except Exception as exc:
        try:
            notify_failure(settings, exc)
        except Exception as notice_error:
            logging.error("Could not send weekly failure notice: %s", type(notice_error).__name__)
        raise
