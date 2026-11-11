"""Remove one published article by a guarded revert PR; preserve later site work."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .publication_delivery import locked_publication, publication_path
from .settings import Settings
from .site_preview import REPOSITORY, command, require_ci_quality
from .slack_guard import safe_client


def handle_undo(settings: Settings, body: dict[str, Any], raw_client: Any) -> bool:
    """Authenticate workspace, user, message, article and deployed commit before queuing."""
    if not (
        settings.production_auto_publish_enabled
        and settings.publishing_enabled
        and settings.production_delivery_enabled
    ):
        return False
    action = (body.get("actions") or [{}])[0]
    value = str(action.get("value", ""))
    if value.count(":") != 1 or action.get("action_id") != "geo_publication_undo":
        return False
    draft_id, commit = value.split(":")
    try:
        path = publication_path(settings, draft_id)
    except ValueError:
        return False
    if not path.exists():
        return False
    with locked_publication(path) as record:
        user = body.get("user", {}).get("id")
        if (
            body.get("team", {}).get("id") != settings.slack_team_id
            or body.get("channel", {}).get("id") != settings.slack_production_channel_id
            or body.get("message", {}).get("ts") != record.get("message_ts")
            or user not in settings.approvers
            or record.get("production_commit") != commit
            or record.get("state") != "published"
        ):
            return False
        record.update(state="undo_queued", undo_requested_by=user)
    # Save durable intent first. A failed UI update cannot lose the Undo request.
    try:
        safe_client(
            settings, raw_client, production_review_message_ts=record["message_ts"]
        ).chat_update(
            channel=settings.slack_production_channel_id,
            ts=record["message_ts"],
            text="Undo publication is queued. The article is still live until removal is verified.",
            blocks=[],
        )
    except Exception:
        pass
    return True


def prepare_revert(settings: Settings, record: dict[str, Any]) -> dict[str, Any]:
    """Revert only the publication commit on current main, then run normal remote checks."""
    from .production_publish import api

    root = settings.storage_dir / record["draft_id"]
    checkout = root / "undo-website"
    if checkout.exists():
        raise ValueError("Undo checkout exists; reconcile before retrying")
    command(
        [
            "git",
            "clone",
            "--branch",
            "main",
            f"https://github.com/{REPOSITORY}.git",
            str(checkout.resolve()),
        ],
        root,
    )
    base = command(["git", "rev-parse", "HEAD"], checkout)
    commit = record["production_commit"]
    parents = command(["git", "rev-list", "--parents", "-n", "1", commit], checkout).split()
    if len(parents) != 3:
        raise ValueError("Expected the exact two-parent publication merge")
    changed = set(
        command(["git", "diff", "--name-only", commit + "^1", commit], checkout).splitlines()
    )
    allowed = {"src/data/posts.js", f"public/blog/media/{record['slug']}-hero.webp"}
    if not changed or not changed <= allowed:
        raise ValueError("Publication commit contains unrelated changes; automatic Undo refused")
    # Verify it is our article addition rather than an arbitrary registry edit.
    old = command(["git", "show", commit + "^1:src/data/posts.js"], checkout)
    new = command(["git", "show", commit + ":src/data/posts.js"], checkout)
    from .undo_registry import verify_article_addition

    verify_article_addition(old, new, record["slug"])
    branch = "feat/blog-agent-undo-" + record["draft_id"]
    command(["git", "switch", "-c", branch], checkout)
    command(["git", "revert", "--no-commit", "-m", "1", commit], checkout)
    reverted = set(command(["git", "diff", "--cached", "--name-only"], checkout).splitlines())
    if reverted != changed:
        raise ValueError("Undo diff differs from the original article files")
    command(["git", "commit", "-m", "fix(blog): undo publication " + record["draft_id"]], checkout)
    head = command(["git", "rev-parse", "HEAD"], checkout)
    command(["git", "push", "-u", "origin", branch], checkout)
    body = root / "undo-pr-body.txt"
    body.write_text(
        "Removes the single article requested through its authenticated Undo publication button.\n\nReverts its original merge on current main; unrelated subsequent work is preserved.\nValidation: exact-commit blog quality workflow and normal branch protections.\n"
    )
    url = command(
        [
            "gh",
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            branch,
            "--title",
            "Undo blog publication: " + record["title"],
            "--body-file",
            str(body.resolve()),
        ],
        checkout,
    )
    number = int(url.rstrip("/").rsplit("/", 1)[1])
    require_ci_quality(checkout, head)
    pr = api(checkout, f"repos/{REPOSITORY}/pulls/{number}")
    from .production_publish import validate_pr

    plan = dict(head_sha=head, base_sha=base)
    main = api(checkout, f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"]
    validate_pr(pr, plan, main)
    return dict(undo_head=head, undo_base=base, undo_pr=number)


def complete_source(settings: Settings, record: dict[str, Any]) -> None:
    """Hold the owned research topic after removal, so it cannot be republished automatically."""
    from .research_api import ResearchAPI
    from .research_lifecycle import same_time
    from .store import Store

    payload = json.loads((settings.storage_dir / record["draft_id"] / "payload.json").read_text())
    topic = payload.get("topic", {})
    if topic.get("research_record_id"):
        with ResearchAPI(settings) as api:
            fields = api.get(topic["research_record_id"])["fields"]
            if (
                fields.get("Status") != "Published"
                or not same_time(fields.get("Claimed at", ""), topic.get("research_claimed_at", ""))
                or fields.get("Published URL") != record["live_url"]
            ):
                raise ValueError("Research ownership changed after publication")
            api.patch(
                topic["research_record_id"],
                {
                    "Status": "Held",
                    "Published URL": None,
                    "Published date": None,
                    "Review notes": (
                        str(fields.get("Review notes", ""))
                        + "\nPublication undone by authorized user; review before reproposing."
                    ).strip(),
                },
            )
    with Store(settings.storage_dir).db() as db:
        db.execute("UPDATE drafts SET status='unpublished' WHERE id=?", (record["draft_id"],))


def run_pending(settings: Settings) -> None:
    """Resume merged removals; uncertain pre-merge operations stop for reconciliation."""
    from .production_publish import api, locked_review, validate_pr, wait_production
    from .weekly import private_alert

    for path in sorted((settings.storage_dir / "publications").glob("*.json")):
        with locked_publication(path) as record:
            if record.get("state") not in {
                "undo_queued",
                "undo_preparing",
                "undo_ready",
                "undo_merging",
                "undo_merged",
            }:
                continue
            try:
                if record["state"] == "undo_preparing":
                    raise ValueError("Interrupted Undo preparation requires reconciliation")
                if record["state"] == "undo_queued":
                    record["state"] = "undo_preparing"
                    path.write_text(json.dumps(record, indent=2))
                    record.update(prepare_revert(settings, record), state="undo_ready")
                    path.write_text(json.dumps(record, indent=2))
                checkout = (settings.storage_dir / record["draft_id"] / "undo-website").resolve()
                if record["state"] in {"undo_ready", "undo_merging"}:
                    pr = api(checkout, f"repos/{REPOSITORY}/pulls/{record['undo_pr']}")
                    if pr.get("merged"):
                        if pr["head"]["sha"] != record["undo_head"]:
                            raise ValueError("Unexpected merged Undo version")
                        sha = pr["merge_commit_sha"]
                    else:
                        require_ci_quality(checkout, record["undo_head"])
                        main = api(checkout, f"repos/{REPOSITORY}/git/ref/heads/main")["object"][
                            "sha"
                        ]
                        validate_pr(
                            pr,
                            dict(head_sha=record["undo_head"], base_sha=record["undo_base"]),
                            main,
                        )
                        record["state"] = "undo_merging"
                        path.write_text(json.dumps(record, indent=2))
                        merged = api(
                            checkout,
                            f"repos/{REPOSITORY}/pulls/{record['undo_pr']}/merge",
                            "-X",
                            "PUT",
                            "-f",
                            "sha=" + record["undo_head"],
                            "-f",
                            "merge_method=merge",
                        )
                        if not merged.get("merged"):
                            raise ValueError("Undo PR not merged")
                        sha = merged["sha"]
                    record.update(state="undo_merged", undo_commit=sha)
                    path.write_text(json.dumps(record, indent=2))
                wait_production(checkout, record["undo_commit"])
                with httpx.Client(timeout=45, follow_redirects=False, trust_env=False) as client:
                    response = client.get(record["live_url"])
                    if response.status_code not in {404, 410}:
                        raise ValueError("Article removal is not verified live")
                from .research_queue import live_paths

                if "/blog/" + record["slug"] in live_paths():
                    raise ValueError("Removed article remains in the sitemap")
                record["state"] = "unpublished"
                path.write_text(json.dumps(record, indent=2))
                review_path = settings.storage_dir / "production-review.json"
                if review_path.exists():
                    with locked_review(settings) as (current_path, current):
                        if current.get("draft_id") == record["draft_id"]:
                            current["state"] = "unpublished"
                            current_path.write_text(json.dumps(current, indent=2))
                try:
                    complete_source(settings, record)
                except Exception as exc:
                    record["source_sync_error"] = type(exc).__name__
                    private_alert(
                        settings, "The article was removed, but its source status needs inspection."
                    )
                if record.get("message_ts"):
                    safe_client(
                        settings, production_review_message_ts=record["message_ts"]
                    ).chat_update(
                        channel=settings.slack_production_channel_id,
                        ts=record["message_ts"],
                        text=f"Publication undone: {record['title']}. Removal from the website is verified.",
                        blocks=[],
                    )
            except Exception as exc:
                if record.get("state") == "unpublished":
                    record["notification_error"] = type(exc).__name__
                    private_alert(
                        settings,
                        "Article removal is verified, but updating its Slack card needs inspection.",
                    )
                else:
                    record.update(state="undo_needs_inspection", undo_error=type(exc).__name__)
                    private_alert(
                        settings,
                        "Undo publication needs inspection. Article removal has not been confirmed; no automatic retry will occur.",
                    )
