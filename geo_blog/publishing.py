"""Publish only an explicitly approved, immutable article; never merge preview branches."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings
    from .store import Store
import fcntl
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx
import yaml

from .preview_pipeline import command
from .slack_guard import safe_client
from .website import article_location, flat_routes


def digest(value: Any) -> str:
    """Hash a reviewed payload to detect changes before a future publication."""
    return hashlib.sha256(value.encode()).hexdigest()


def queue_in_transaction(db: Any, row: dict[str, Any]) -> Any:
    """Called inside the same transaction that records the user's decision."""
    if row["status"] != "approved" or not row["reviewer"] or not row["reviewed_at"]:
        raise ValueError("An exact saved approval is required")
    key = digest(row["id"] + ":" + row["message_ts"])[:24]
    db.execute(
        "INSERT OR IGNORE INTO publish_jobs(id,draft_id,state,payload,reviewer,reviewed_at,message_ts,channel,thread_ts) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            key,
            row["id"],
            "queued",
            row["payload"],
            row["reviewer"],
            row["reviewed_at"],
            row["message_ts"],
            row["channel"],
            row["thread_ts"],
        ),
    )
    db.execute(
        "UPDATE drafts SET status='publishing' WHERE id=? AND status='approved'",
        (row["id"],),
    )
    return key


def enqueue_existing(settings: Settings, store: Store, draft_id: str) -> Any:
    """Require an existing reviewed draft before reserving a future publication job."""
    if not settings.publishing_enabled:
        raise ValueError("Publishing is disabled")
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if (
            not row
            or row["channel"] != settings.slack_channel_id
            or row["reviewer"] not in settings.approvers
        ):
            raise ValueError("Configured reviewer and channel required")
        return queue_in_transaction(db, row)


def retry_failed(settings: Settings, store: Store, draft_id: str) -> Any:
    """Operator recovery resumes the same immutable job, including an already merged PR."""
    if not settings.publishing_enabled:
        raise ValueError("Publishing is disabled")
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        job = db.execute(
            "SELECT * FROM publish_jobs WHERE draft_id=? AND state='failed' ORDER BY rowid DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if (
            not row
            or not job
            or row["status"] != "publish_failed"
            or any(row[k] != job[k] for k in ("payload", "reviewer", "reviewed_at", "message_ts"))
        ):
            raise ValueError("No unchanged failed publication to resume")
        db.execute("UPDATE publish_jobs SET state='queued',error=NULL WHERE id=?", (job["id"],))
        db.execute("UPDATE drafts SET status='publishing' WHERE id=?", (draft_id,))
        return job["id"]


def approved_source(settings: Settings, job: dict[str, Any]) -> Any:
    """Match the checked deployment to the exact payload the reviewer approved."""
    payload = json.loads(job["payload"])
    url = payload.get("preview_url")
    candidates = [settings.storage_dir / job["draft_id"] / "deployment.json"]
    candidates.extend((settings.storage_dir / "revisions").glob("*/*/deployment.json"))
    for manifest in candidates:
        if not manifest.exists():
            continue
        state = json.loads(manifest.read_text())
        if state.get("preview_url") != url:
            continue
        folder = manifest.parent
        exported = json.loads((folder / "export.json").read_text())
        qa = json.loads((folder / "hosted-qa.json").read_text())
        expected = digest(payload["markdown"])
        if state["article_hash"] != expected or exported["article_hash"] != expected:
            raise ValueError("Approved article differs from checked source")
        if (
            qa["sha"] != state["sha"]
            or qa["url"] != url
            or not all(
                qa["scores"].get(k, 0) >= v
                for k, v in {
                    "performance": 90,
                    "accessibility": 95,
                    "best-practices": 95,
                }.items()
            )
        ):
            raise ValueError("Approved preview has no matching passed audit")
        path = state["path"]
        location = article_location(payload)
        if path != location["path"] or exported["path"] != path:
            raise ValueError("Unsafe article path")
        relative = location["mdx_path"]
        if exported.get("mdx_path", relative) != relative:
            raise ValueError("Checked content path changed")
        source = folder / "website"
        raw = (source / relative).read_bytes()
        if hashlib.sha256(raw).hexdigest() != exported["mdx_hash"]:
            raise ValueError("Checked article was modified")
        log = folder / "publish-source-check.log"
        if command(["git", "rev-parse", "HEAD"], source, log) != state["sha"]:
            raise ValueError("Checked source commit changed")
        checked_paths = [relative, "public/blog/media"]
        if location["route_path"]:
            for name, content in flat_routes(location["hub"], location["slug"]).items():
                route = source / name
                if route.is_symlink() or route.read_text() != content:
                    raise ValueError("Checked article route was modified")
                checked_paths.append(name)
        if command(["git", "status", "--porcelain", "--", *checked_paths], source, log):
            raise ValueError("Checked source has uncommitted changes")
        return source, relative, raw, state
    raise ValueError("No checked deployment matches this approval")


def make_public(raw: bytes, date: Any) -> Any:
    """Change only draft visibility and date in an already reviewed article."""
    text = raw.decode()
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front)
    if meta.get("draft") is not True:
        raise ValueError("Expected an unpublished reviewed article")
    meta["draft"] = False
    meta["date"] = date
    return (
        "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---" + body
    ).encode(), meta


def copy_article(source: Path, checkout: Path, relative: Any, raw: bytes, date: Any) -> Any:
    """Copy the reviewed MDX, images and, if needed, its deterministic flat route."""
    if not re.fullmatch(r"src/content/blog/[a-z0-9-]+/[a-z0-9-]+\.mdx", relative):
        raise ValueError("Unsafe article destination")
    content, meta = make_public(raw, date)
    routes = {}
    target = checkout / relative
    if meta.get("path"):
        hub = Path(relative).parent.name
        slug = Path(relative).stem
        if meta["path"] != f"/blog/{slug}/":
            raise ValueError("Flat route does not match article slug")
        routes = flat_routes(hub, slug)
        for name, route_content in routes.items():
            origin = source / name
            destination = checkout / name
            if origin.is_symlink() or origin.read_text() != route_content:
                raise ValueError("Unreviewed flat route code")
            if destination.exists() and destination.read_text() != route_content:
                raise ValueError("Would overwrite an existing website route")
            if destination.parent.exists() and not target.exists():
                raise ValueError("Flat route conflicts with existing website directory")
    if target.exists() and target.read_bytes() != content:
        raise ValueError("Article URL already exists with different content")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    paths = [relative]
    for name, route_content in routes.items():
        destination = checkout / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(route_content)
        paths.append(name)
    for key in ("hero", "diagram"):
        if not meta.get(key):
            continue
        media = meta[key]["src"]
        if not re.fullmatch(r"/blog/media/[a-z0-9-]+\.(webp|png|jpg|svg)", media):
            raise ValueError("Unsafe media path")
        name = "public" + media
        origin = source / name
        dest = checkout / name
        if origin.is_symlink():
            raise ValueError("Symlink media is not supported")
        data = origin.read_bytes()
        if dest.exists() and dest.read_bytes() != data:
            raise ValueError("Would overwrite existing artwork")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        paths.append(name)
    return paths, meta


def wait_deployment(
    settings: Settings, sha: str, environment: str, checkout: Path, log: Path
) -> Any:
    """Poll for the exact deployment version without silently accepting another build."""
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        deployments = json.loads(
            command(
                [
                    "gh",
                    "api",
                    f"repos/{settings.website_repository}/deployments?sha={sha}&per_page=20",
                ],
                checkout,
                log,
            )
        )
        for deployment in deployments:
            if deployment["sha"] != sha or deployment["environment"].lower() != environment:
                continue
            statuses = json.loads(
                command(
                    [
                        "gh",
                        "api",
                        f"repos/{settings.website_repository}/deployments/{deployment['id']}/statuses",
                    ],
                    checkout,
                    log,
                )
            )
            if not statuses:
                continue
            status = statuses[0]
            if status["state"] in {"failure", "error"}:
                raise RuntimeError("Website deployment failed")
            if status["state"] == "success":
                return status["environment_url"].rstrip("/")
        time.sleep(10)
    raise TimeoutError("Website deployment is not ready yet")


def wait_required_checks(settings: Settings, sha: str, checkout: Path, log: Path) -> None:
    """Honor branch checks explicitly even when the authenticated account is an admin."""
    protection = json.loads(
        command(
            [
                "gh",
                "api",
                f"repos/{settings.website_repository}/branches/{settings.website_production_branch}/protection",
            ],
            checkout,
            log,
        )
    )
    required = protection.get("required_status_checks", {}).get("contexts", [])
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        runs = json.loads(
            command(
                [
                    "gh",
                    "api",
                    f"repos/{settings.website_repository}/commits/{sha}/check-runs?per_page=100",
                ],
                checkout,
                log,
            )
        )["check_runs"]
        statuses = json.loads(
            command(
                [
                    "gh",
                    "api",
                    f"repos/{settings.website_repository}/commits/{sha}/status",
                ],
                checkout,
                log,
            )
        )["statuses"]
        actual = {}
        for run in sorted(runs, key=lambda r: r["id"]):
            actual[run["name"]] = run["conclusion"] if run["status"] == "completed" else "pending"
        for status in reversed(statuses):
            actual[status["context"]] = status["state"]
        if any(
            actual.get(name)
            in {
                "failure",
                "error",
                "cancelled",
                "timed_out",
                "action_required",
                "stale",
            }
            for name in required
        ):
            raise RuntimeError("Required website checks failed; publication is held")
        if required and all(actual.get(name) == "success" for name in required):
            return
        if not required:
            return
        time.sleep(15)
    raise TimeoutError("Required website checks are still pending")


def assert_merge_ready(pr: Any, settings: Settings, expected_sha: Any) -> None:
    """Validate destination and branch checks; this helper does not perform a merge."""
    # Even an admin must not bypass reviews, conflicts or other branch rules.
    if (
        pr.get("head", {}).get("sha") != expected_sha
        or pr.get("base", {}).get("ref") != settings.website_production_branch
    ):
        raise ValueError("Publication branch or destination changed")
    if pr.get("mergeable_state") != "clean" or pr.get("mergeable") is not True:
        raise RuntimeError("Website branch rules are not satisfied; publication is held")


def verify_live(url: str, title: str, hero: Any, client: Any = None) -> None:
    """Check the public article, canonical, indexing, image, listing and sitemap."""
    from bs4 import BeautifulSoup

    client = client or httpx.Client(timeout=45, follow_redirects=True)
    response = client.get(url)
    response.raise_for_status()
    if str(response.url).rstrip("/") != url.rstrip("/"):
        raise ValueError("Live page redirected elsewhere")
    page = BeautifulSoup(response.text, "html.parser")
    heading = page.find("h1")
    if not heading or heading.get_text(" ", strip=True) != title:
        raise ValueError("Live article title mismatch")
    if (
        any(
            "noindex" in str(tag.get("content", "")).lower()
            for tag in page.select('meta[name="robots"],meta[name="googlebot"]')
        )
        or "noindex" in response.headers.get("x-robots-tag", "").lower()
    ):
        raise ValueError("Live article is not indexable")
    canonical = page.select_one('link[rel="canonical"]')
    if not canonical or str(canonical.get("href", "")).rstrip("/") != url.rstrip("/"):
        raise ValueError("Wrong live canonical")
    if hero:
        if not page.select_one("article img"):
            raise ValueError("Hero missing")
        image = client.get("https://geo-insulation.com" + hero["src"])
        image.raise_for_status()
        if not image.headers.get("content-type", "").startswith("image/"):
            raise ValueError("Hero is not an image")
    listing = client.get("https://geo-insulation.com/blog/")
    listing.raise_for_status()
    if not BeautifulSoup(listing.text, "html.parser").find("a", href=urlparse(url).path):
        raise ValueError("Article missing from blog listing")
    from urllib.robotparser import RobotFileParser

    robots = client.get("https://geo-insulation.com/robots.txt")
    robots.raise_for_status()
    rules = RobotFileParser()
    rules.parse(robots.text.splitlines())
    if not rules.can_fetch("Googlebot", url):
        raise ValueError("Robots rules block the live article")
    sitemap = client.get("https://geo-insulation.com/sitemap.xml")
    sitemap.raise_for_status()
    if url not in sitemap.text:
        raise ValueError("Article missing from sitemap")


# A row the agent was allowed to draft from. Anything else is a human editorial decision.
DRAFTABLE_STATUSES = {"", "ready", "proposed", "not started", "todo", "to do"}


def mark_airtable_published(
    settings: Settings, job: dict[str, Any], folder: Path, client: Any = None
) -> Any:
    """Record the published state on the source keyword row.

    Writes the Status field and nothing else, and only when the row still holds
    a status the agent was allowed to draft from. It never overwrites a human
    editorial state, and it never runs before a publication is verified live.
    """
    record = (json.loads(job["payload"]).get("topic") or {}).get("id") or ""
    if not settings.airtable_write_enabled:
        return {"updated": False, "reason": "Airtable writing is turned off"}
    if not settings.airtable_base_id or not re.fullmatch(r"rec[A-Za-z0-9]+", record):
        return {"updated": False, "reason": "This draft has no Airtable keyword row"}
    endpoint = (
        "https://api.airtable.com/v0/"
        + quote(settings.airtable_base_id, safe="")
        + "/"
        + quote(settings.airtable_table, safe="")
        + "/"
        + quote(record, safe="")
    )
    headers = {"Authorization": "Bearer " + settings.airtable_token.get_secret_value()}
    with httpx.Client(timeout=30) if client is None else client as http:
        response = http.get(endpoint, headers=headers)
        response.raise_for_status()
        before = response.json().get("fields", {})
        status = str(before.get("Status", "")).strip()
        if status.casefold() == "published":
            result = {"updated": False, "reason": "The row already reads Published"}
        elif status.casefold() not in DRAFTABLE_STATUSES:
            result = {"updated": False, "reason": "Left the editorial status unchanged"}
        else:
            response = http.patch(
                endpoint, headers=headers, json={"fields": {"Status": "Published"}}
            )
            response.raise_for_status()
            response = http.get(endpoint, headers=headers)
            response.raise_for_status()
            after = response.json().get("fields", {})
            changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
            if changed != {"Status"} or str(after.get("Status", "")).strip() != "Published":
                raise ValueError("Airtable did not change the status field alone")
            result = {"updated": True, "changed_fields": sorted(changed)}
        result.update(
            record=record,
            previous_status=status,
            at=datetime.now(timezone.utc).isoformat(),
        )
        (folder / "airtable-status.json").write_text(
            json.dumps({"before": before, "result": result}, indent=2)
        )
        return result


def publish(settings: Settings, store: Store, job: dict[str, Any], folder: Path) -> Any:
    """Reject publication until the real website and human PR workflow are connected."""
    raise RuntimeError("Website integration is pending; deployment is disabled in this release")


def run_publication(settings: Settings, store: Store, job: dict[str, Any], folder: Path) -> None:
    """Production integration must open a draft PR; automated merges are not supported.

    The supplied starter targeted a different website contract. Keep publishing
    disabled until the real site's content loader and human PR review are wired.
    """
    raise RuntimeError("Website integration is pending; deployment is disabled in this release")


def run_next(
    settings: Settings, store: Store, client: Any = None, publish_fn: Any = publish
) -> Any:
    """Run next."""
    if not settings.publishing_enabled:
        return
    with (settings.storage_dir / "nightly.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with store.db() as db:
            row = db.execute(
                "SELECT * FROM publish_jobs WHERE state IN ('queued','working') ORDER BY rowid LIMIT 1"
            ).fetchone()
            if not row:
                return
            job = dict(row)
            current = db.execute("SELECT * FROM drafts WHERE id=?", (job["draft_id"],)).fetchone()
            if current["status"] != "publishing" or any(
                current[k] != job[k] for k in ("payload", "reviewer", "reviewed_at", "message_ts")
            ):
                db.execute(
                    "UPDATE publish_jobs SET state='cancelled',error='Approval changed' WHERE id=?",
                    (job["id"],),
                )
                return
            db.execute("UPDATE publish_jobs SET state='working' WHERE id=?", (job["id"],))
        client = safe_client(settings, client)
        if client.auth_test()["team_id"] != settings.slack_team_id:
            raise ValueError("Wrong Slack workspace")
        folder = (settings.storage_dir / "publishing" / job["id"]).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            live = publish_fn(settings, store, job, folder)
        except Exception as exc:
            with store.db() as db:
                db.execute(
                    "UPDATE publish_jobs SET state='failed',error=? WHERE id=?",
                    (type(exc).__name__ + ": " + str(exc), job["id"]),
                )
                db.execute(
                    "UPDATE drafts SET status='publish_failed' WHERE id=? AND status='publishing'",
                    (job["draft_id"],),
                )
            client.chat_postMessage(
                channel=job["channel"],
                thread_ts=job["thread_ts"],
                reply_broadcast=False,
                text="Your approval is saved, but I couldn’t complete publishing and verify the live page. I’ll need to resolve this before I can confirm it is live.",
                unfurl_links=False,
                unfurl_media=False,
            )
            raise
        with store.db() as db:
            payload = json.loads(job["payload"])
            payload["live_url"] = live
            db.execute(
                "UPDATE drafts SET status='published',payload=? WHERE id=?",
                (json.dumps(payload), job["draft_id"]),
            )
            # Claim delivery before Slack call: an uncertain network response cannot duplicate the announcement.
            db.execute(
                "UPDATE publish_jobs SET state='notifying',live_url=? WHERE id=?",
                (live, job["id"]),
            )
        # The article is already live and verified. A keyword-row update cannot undo or delay that.
        try:
            marked = mark_airtable_published(settings, job, folder)
        except Exception as exc:
            logging.exception("Could not mark the Airtable keyword row published")
            marked = {
                "updated": False,
                "reason": "The keyword list did not accept the change (" + type(exc).__name__ + ")",
            }
        text = f"Your approved blog is live: <{live}|Read it on Geo Insulation>."
        if (
            settings.airtable_write_enabled
            and not marked.get("updated")
            and marked.get("reason") != "The row already reads Published"
        ):
            text += " I could not mark its keyword row Published, so that row still needs a manual change."
        result = client.chat_postMessage(
            channel=job["channel"],
            thread_ts=job["thread_ts"],
            reply_broadcast=False,
            text=text,
            unfurl_links=False,
            unfurl_media=False,
        )
        with store.db() as db:
            db.execute(
                "UPDATE publish_jobs SET state='complete',notification_ts=? WHERE id=?",
                (result["ts"], job["id"]),
            )
        return live
