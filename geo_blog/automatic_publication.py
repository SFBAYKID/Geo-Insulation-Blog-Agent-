"""Owner-authorized automatic publication after the existing article quality gates."""

from __future__ import annotations

import json
from typing import Any

from .settings import Settings

POLICY = "owner-auto-publish-2026-11-10"


def arm(
    settings: Settings, draft_id: str, commit: str, base_sha: str, pr_number: int
) -> dict[str, Any]:
    """Queue a checked version without posting a review request to customers."""
    from .production_publish import comments_digest
    from .weekly import archive_previous

    archive_previous(settings)
    review: dict[str, Any] = dict(
        state="queued",
        draft_id=draft_id,
        preview_commit=commit,
        publication_armed=True,
        publication_policy=POLICY,
        comments=[],
        blocks=[],
    )
    review["approved_comments_digest"] = comments_digest(review)
    plan = dict(
        armed=True,
        draft_id=draft_id,
        head_sha=commit,
        base_sha=base_sha,
        pr_number=pr_number,
        publication_policy=POLICY,
    )
    path = settings.storage_dir / "production-review.json"
    import fcntl

    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        (settings.storage_dir / "publication-plan.json").write_text(json.dumps(plan, indent=2))
        path.write_text(json.dumps(review, indent=2))
    return dict(state="complete", **plan)


def migrate_pending(settings: Settings, expected_draft: str) -> None:
    """Convert only the explicitly requested current draft, retaining its Slack message IDs."""
    from .production_publish import comments_digest, locked_review
    from .slack_guard import safe_client

    with locked_review(settings) as (path, review):
        if review["draft_id"] != expected_draft:
            raise ValueError("Current draft changed; migration stopped")
        if review.get("publication_policy") == POLICY:
            return
        if review.get("state") != "pending" or review.get("comments"):
            raise ValueError("Draft has a decision or feedback; inspect before migration")
        plan_path = settings.storage_dir / "publication-plan.json"
        plan = json.loads(plan_path.read_text())
        if plan["draft_id"] != expected_draft or plan["head_sha"] != review["preview_commit"]:
            raise ValueError("Current publication plan differs")
        text = "This blog will publish automatically after final checks. The live link and Undo publication button will appear here."
        for ts in {review.get("message_ts"), review.get("thread_ts")} - {None, ""}:
            safe_client(settings, production_review_message_ts=str(ts)).chat_update(
                channel=settings.slack_production_channel_id, ts=ts, text=text, blocks=[]
            )
        review.update(
            state="queued",
            publication_policy=POLICY,
            approved_comments_digest=comments_digest(review),
        )
        plan["publication_policy"] = POLICY
        plan_path.write_text(json.dumps(plan, indent=2))
        path.write_text(json.dumps(review, indent=2))
