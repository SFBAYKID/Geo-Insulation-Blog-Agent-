"""Run one explicitly configured review workflow per Pacific Wednesday."""

from __future__ import annotations

import argparse
import fcntl
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .settings import Settings
from .slack_guard import require_test_channel
from .store import Store

ZONE = ZoneInfo("America/Los_Angeles")


def readiness(settings: Settings, *, production: bool = False) -> list[str]:
    """List missing runtime prerequisites without spending or contacting providers."""
    missing = []
    require_test_channel(settings, settings.slack_channel_id)
    if not production and (settings.production_delivery_enabled or settings.publishing_enabled):
        raise ValueError("Scheduled playground runs require production delivery disabled")
    if production:
        for field in (
            "production_delivery_enabled",
            "publishing_enabled",
            "slack_production_channel_id",
        ):
            if not getattr(settings, field):
                missing.append(field)
        if settings.website_base_branch != "main":
            missing.append("website_base_branch=main")
    for name in (
        "openai_api_key",
        "slack_bot_token",
        "slack_team_id",
        "slack_approver_ids",
        "website_repository",
        "website_preview_enabled",
        "image_generation_enabled",
    ):
        try:
            settings.require(name)
        except ValueError:
            missing.append(name)
    fields = (
        (
            "basecamp_client_id",
            "basecamp_client_secret",
            "basecamp_refresh_token",
            "basecamp_account_id",
            "basecamp_project_id",
            "basecamp_todolist_id",
        )
        if settings.blog_queue_source == "basecamp"
        else ("airtable_token", "airtable_base_id")
    )
    for name in fields:
        try:
            settings.require(name)
        except ValueError:
            missing.append(name)
    return missing


def run(
    settings: Settings, *, now: datetime | None = None, production: bool = False
) -> dict[str, Any]:
    """Use a timezone gate and durable date receipt to avoid DST and retry duplicates."""
    current = (now or datetime.now(ZONE)).astimezone(ZONE)
    result: dict[str, Any]
    if current.weekday() != 2 or current.hour != 9 or current.minute != 0:
        return {"status": "outside_schedule"}
    folder = settings.storage_dir / ("scheduled-production" if production else "scheduled-previews")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (current.date().isoformat() + ".json")
    with (folder / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "already_running"}
        if path.exists():
            return {"status": "already_attempted", "receipt": str(path)}
        missing = readiness(settings, production=True) if production else readiness(settings)
        if missing:
            result = {"status": "not_ready", "missing": missing}
            path.write_text(json.dumps(result, indent=2))
            return result
        # Persist before external reads, generation or delivery. Never retry an
        # ambiguous scheduled run automatically; keep its evidence for recovery.
        path.write_text(json.dumps({"status": "running"}))
        try:
            if production:
                from .weekly import run_weekly_notifying

                draft_id = run_weekly_notifying(settings, Store(settings.storage_dir))
            else:
                from .cli import run_daily

                draft_id = run_daily(settings, Store(settings.storage_dir), send=True)
            result = {"status": "delivered" if draft_id else "queue_empty", "draft_id": draft_id}
        except Exception as exc:
            result = {"status": "needs_inspection", "error": type(exc).__name__}
        path.write_text(json.dumps(result, indent=2))
        return result


def main() -> None:
    """Load only Geo's explicit runtime env file for cron or systemd invocation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--production", action="store_true")
    args = parser.parse_args()
    options: dict[str, Any] = {"_env_file": args.env_file}
    settings = Settings(**options)
    print(
        json.dumps(
            {"missing": readiness(settings, production=args.production)}
            if args.check
            else run(settings, production=args.production)
        )
    )


if __name__ == "__main__":
    main()
