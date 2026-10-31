"""Read the Geo research queue and notify Chase without mutating research records."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .settings import Settings
from .slack_guard import safe_client
from .weekly import CHASE

BASE = "appJnXLPZjVEfvWiL"
TABLE = "tbll5xV6zwbZSMSjR"
LINK = f"https://airtable.com/{BASE}/{TABLE}"


def proposed_count(settings: Settings, client: Any = None) -> int:
    """Count exact Proposed rows across pages; access failures are never empty queues."""
    settings.require("airtable_token")
    if settings.airtable_base_id != BASE or settings.airtable_table != TABLE:
        raise ValueError("Research queue monitor requires the verified Geo base and table")
    params: dict[str, Any] = {"pageSize": 100}
    seen: set[str] = set()
    offsets: set[str] = set()
    with httpx.Client(timeout=30) if client is None else client as http:
        while True:
            response = http.get(
                f"https://api.airtable.com/v0/{BASE}/{TABLE}",
                headers={"Authorization": "Bearer " + settings.airtable_token.get_secret_value()},
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("records"), list):
                raise ValueError("Invalid research queue response")
            for row in data["records"]:
                if not isinstance(row.get("fields", {}), dict) or not row.get("id"):
                    raise ValueError("Invalid research queue record")
                if row.get("fields", {}).get("Status") == "Proposed":
                    seen.add(row["id"])
            offset = data.get("offset")
            if not offset:
                return len(seen)
            if not isinstance(offset, str) or offset in offsets:
                raise ValueError("Invalid research queue pagination")
            offsets.add(offset)
            params["offset"] = offset


def check_queue(settings: Settings, *, client: Any = None, slack: Any = None) -> None:
    """Alert once per Pacific date/count; reserve before send to preserve ambiguous sends."""
    if not settings.research_queue_monitor_enabled:
        return
    try:
        count = proposed_count(settings, client)
        if count > settings.research_queue_low_threshold:
            return
        category = str(count)
        description = (
            "The Geo research queue is empty: no approved (Proposed) topics remain. "
            "Please have the research queue replenished and approved."
            if count == 0
            else f"The Geo research queue is running low: {count} approved "
            "(Proposed) topics remain. Please arrange more approved topics before it runs out."
        )
        text = description + f" Held topics are not available to write. Queue: {LINK}"
    except Exception as exc:
        category = "read-error"
        text = (
            "I could not check the Geo research queue; this does not mean it is empty. "
            f"The connection needs inspection ({type(exc).__name__})."
        )
    day = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    folder = settings.storage_dir / "research-queue-notices"
    folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / f"{day}-{category}.json"
    try:
        with receipt.open("x") as file:
            json.dump({"state": "sending", "category": category}, file)
    except FileExistsError:
        return
    try:
        guarded = safe_client(settings, slack)
        if guarded.auth_test()["team_id"] != settings.slack_team_id:
            raise ValueError("Slack workspace mismatch")
        result = guarded.chat_postMessage(
            channel=settings.slack_test_channel_id,
            text=f"<@{CHASE}> {text}",
            unfurl_links=False,
            unfurl_media=False,
        )
        receipt.write_text(json.dumps({"state": "sent", "message_ts": result["ts"]}))
    except Exception as exc:
        receipt.write_text(json.dumps({"state": "needs_inspection", "error": type(exc).__name__}))
        logging.error("Research queue notice needs inspection: %s", type(exc).__name__)
