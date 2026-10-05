"""Publish an explicitly armed, exact-commit Slack approval through the real website PR.

No content is generated here. A changed branch/base, failed CI, revoked approval,
new feedback or ambiguous provider failure stops publication for inspection.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx

from .settings import Settings
from .site_preview import REPOSITORY, command, require_ci_quality
from .slack_guard import safe_client


def comments_digest(review: dict[str, Any]) -> str:
    """Bind approval to the feedback visible when it was given."""
    return hashlib.sha256(
        json.dumps(review.get("comments", []), sort_keys=True).encode()
    ).hexdigest()


@contextmanager
def locked_review(settings: Settings) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Serialize state transitions with the Slack review/comment handler."""
    path = settings.storage_dir / "production-review.json"
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield path, json.loads(path.read_text())


def validate_approval(
    review: dict[str, Any], plan: dict[str, Any], approvers: set[str] | None = None
) -> None:
    """Reject stale approvals before any GitHub mutation."""

    if (
        not plan.get("armed")
        or review.get("state") not in {"queued", "publishing"}
        or review.get("draft_id") != plan["draft_id"]
        or review.get("preview_commit") != plan["head_sha"]
        or review.get("reviewer") not in (approvers or set())
        or review.get("approved_comments_digest") != comments_digest(review)
    ):
        raise ValueError(
            "Publication requires a current exact-version approval without newer feedback"
        )


def validate_pr(pr: dict[str, Any], plan: dict[str, Any], main_sha: str) -> None:
    """Require the pinned repository, head and production base without bypassing rules."""
    if (
        pr.get("head", {}).get("sha") != plan["head_sha"]
        or pr.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY
        or pr.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY
        or pr.get("base", {}).get("ref") != "main"
        or main_sha != plan["base_sha"]
        or pr.get("draft")
        or pr.get("mergeable") is not True
        or pr.get("mergeable_state") != "clean"
        or pr.get("state") != "open"
    ):
        raise ValueError("Website PR, main branch or required merge checks changed")


def api(checkout: Path, endpoint: str, *args: str) -> Any:
    """Use the existing authenticated GitHub CLI; credentials never enter arguments."""
    return json.loads(command(["gh", "api", endpoint, *args], checkout))


def update_card(settings: Settings, review: dict[str, Any], text: str) -> None:
    """Update the one threaded review card; never duplicate preview links or broadcast."""
    blocks = [b for b in review["blocks"] if b["type"] != "actions"]
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
    safe_client(settings, production_review_message_ts=review["message_ts"]).chat_update(
        channel=settings.slack_production_channel_id,
        ts=review["message_ts"],
        text=text,
        blocks=blocks,
    )


def wait_production(checkout: Path, sha: str) -> None:
    """Require Vercel's successful Production deployment for this exact merge commit."""
    for _ in range(90):
        deployments = api(checkout, f"repos/{REPOSITORY}/deployments?sha={sha}")
        for deployment in deployments:
            if deployment["environment"].lower() != "production":
                continue
            statuses = api(checkout, f"repos/{REPOSITORY}/deployments/{deployment['id']}/statuses")
            if statuses and statuses[0]["state"] == "success":
                return
            if statuses and statuses[0]["state"] in {"failure", "error"}:
                raise RuntimeError("Production deployment failed")
        time.sleep(10)
    raise TimeoutError("Production deployment remains pending")


def verify_live(plan: dict[str, Any], draft: dict[str, Any], checkout: Path, sha: str) -> str:
    """Check actual production HTML, photo, metadata, schema, GA4 and sitemap."""
    from bs4 import BeautifulSoup

    url = "https://geo-insulation.com/blog/" + draft["front_matter"]["slug"]
    with httpx.Client(timeout=45, follow_redirects=True, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        if (
            str(response.url).rstrip("/") != url
            or "noindex" in response.headers.get("x-robots-tag", "").lower()
        ):
            raise ValueError("Live article URL or indexing is incorrect")
        hero = draft["topic"]["hero"]
        image = client.get("https://geo-insulation.com" + hero["src"])
        image.raise_for_status()
        if hashlib.sha256(image.content).hexdigest() != draft["media_provenance"]["sha256"]:
            raise ValueError("Live photo differs from the reviewed photo")
        if not soup.select_one("main img"):
            raise ValueError("Live article has no rendered image")
    command(["git", "fetch", "origin", "main:refs/remotes/origin/main"], checkout)
    # The website's verifier checks title/H1, keywords, indexability, GA4, schema,
    # image alts and sitemap against the checked typed article modules.
    command(
        ["env", "DEPLOYMENT_SHA=" + sha, "node", "scripts/check-published-blog.mjs"],
        checkout / "geo-web",
    )
    return url


def record_basecamp(settings: Settings, draft: dict[str, Any], url: str) -> None:
    """Complete the Basecamp task after verified publication; never undo publication."""
    from .basecamp_complete import record_live

    try:
        # record_live is idempotent (it finds its own comment), so retrying is safe.
        for attempt in range(3):
            try:
                state = record_live(settings, draft, url)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(30 * (attempt + 1))
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error(
            "Basecamp completion needs inspection: %s", type(exc).__name__
        )
        state = "needs_inspection"
        from .weekly import private_alert

        private_alert(
            settings,
            f"The blog is live at {url}, but updating its Basecamp task failed "
            f"({type(exc).__name__}). Ask the blog agent to fix it.",
        )
    with locked_review(settings) as (path, review):
        review["basecamp_state"] = state
        path.write_text(json.dumps(review, indent=2))


def run_once(settings: Settings) -> bool:
    """Resume durable approved work; never replay an uncertain merge without reconciling it."""
    if not settings.production_delivery_enabled:
        return False
    plan_path = settings.storage_dir / "publication-plan.json"
    if not plan_path.exists():
        return False
    plan = json.loads(plan_path.read_text())
    with locked_review(settings) as (path, review):
        if review.get("state") not in {"queued", "publishing", "merged"}:
            return False
        if review["state"] != "merged":
            try:
                validate_approval(review, plan, settings.approvers)
            except ValueError:
                review.update(
                    state="publication_needs_inspection", publication_error="StaleApproval"
                )
                path.write_text(json.dumps(review, indent=2))
                return True
            review["state"] = "publishing"
            path.write_text(json.dumps(review, indent=2))
    checkout = (settings.storage_dir / plan["draft_id"] / "website").resolve()
    root = checkout.parent
    draft = json.loads((root / "payload.json").read_text())
    try:
        if (
            draft["preview_commit"] != plan["head_sha"]
            or command(["git", "rev-parse", "HEAD"], checkout) != plan["head_sha"]
        ):
            raise ValueError("Reviewed source checkout changed")
        if command(["git", "status", "--porcelain"], checkout):
            raise ValueError("Reviewed checkout has uncommitted changes")
        pr = api(checkout, f"repos/{REPOSITORY}/pulls/{plan['pr_number']}")
        if pr.get("merged"):
            # A timeout after GitHub accepted the merge is reconciled by exact head.
            if pr["head"]["sha"] != plan["head_sha"]:
                raise ValueError("A different article version was merged")
            sha = pr["merge_commit_sha"]
        else:
            require_ci_quality(checkout, plan["head_sha"])
            main_sha = api(checkout, f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"]
            validate_pr(pr, plan, main_sha)
            with locked_review(settings) as (path, review):
                validate_approval(review, plan, settings.approvers)
                # GitHub also enforces branch protections and the expected head SHA.
                merged = api(
                    checkout,
                    f"repos/{REPOSITORY}/pulls/{plan['pr_number']}/merge",
                    "-X",
                    "PUT",
                    "-f",
                    "sha=" + plan["head_sha"],
                    "-f",
                    "merge_method=merge",
                )
                if not merged.get("merged"):
                    raise RuntimeError("GitHub did not merge the approved article")
                sha = merged["sha"]
                review.update(state="merged", production_commit=sha)
                path.write_text(json.dumps(review, indent=2))
        with locked_review(settings) as (path, review):
            review.update(state="merged", production_commit=sha)
            path.write_text(json.dumps(review, indent=2))
        wait_production(checkout, sha)
        url = verify_live(plan, draft, checkout, sha)
        with locked_review(settings) as (path, review):
            review.update(state="published", live_url=url, notification_state="sending")
            path.write_text(json.dumps(review, indent=2))
        from .store import Store

        draft.update(live_url=url, production_commit=sha)
        with Store(settings.storage_dir).db() as db:
            db.execute(
                "UPDATE drafts SET status='published',payload=? WHERE id=?",
                (json.dumps(draft), plan["draft_id"]),
            )
        update_card(
            settings,
            review,
            f"Published and verified: <{url}|Read the live blog>. Approved by <@{review['reviewer']}>.",
        )
        with locked_review(settings) as (path, review):
            review["notification_state"] = "sent"
            path.write_text(json.dumps(review, indent=2))
        record_basecamp(settings, draft, url)
    except Exception as exc:
        with locked_review(settings) as (path, review):
            if review.get("state") == "pending":
                return True  # New feedback revoked the queue during checks.
            if review.get("state") == "published":
                review["notification_state"] = "needs_inspection"
                path.write_text(json.dumps(review, indent=2))
                return True
            review.update(
                state="publication_needs_inspection", publication_error=type(exc).__name__
            )
            path.write_text(json.dumps(review, indent=2))
        update_card(
            settings,
            review,
            "Approval is saved, but publication could not be completed and verified. It needs inspection before retrying; I have not confirmed the article live.",
        )
    return True


def start_worker(settings: Settings, stop: threading.Event) -> threading.Thread:
    """The existing single listener owns one restart-resumable publication worker."""

    def work() -> None:
        import logging

        while not stop.is_set():
            try:
                run_once(settings)
            except Exception as exc:
                logging.getLogger(__name__).error(
                    "Publication worker stopped this attempt: %s", type(exc).__name__
                )
            stop.wait(5)

    thread = threading.Thread(target=work, name="geo-approved-publication", daemon=True)
    thread.start()
    return thread
