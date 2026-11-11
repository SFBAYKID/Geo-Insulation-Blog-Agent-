"""Track source ownership across drafting and exact-version production review."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .research_api import ResearchAPI
from .settings import Settings


def same_time(left: str, right: str) -> bool:
    """Compare Airtable's canonical UTC time with the saved claim timestamp."""
    return datetime.fromisoformat(left.replace("Z", "+00:00")) == datetime.fromisoformat(
        right.replace("Z", "+00:00")
    )


def claim(settings: Settings, topic: dict[str, Any], draft_id: str) -> None:
    """Persist claim intent before writing and never blind-retry an uncertain mutation."""
    record = topic.get("research_record_id")
    if not record:
        return
    receipt = settings.storage_dir / draft_id / "research-claim.json"
    if receipt.exists():
        raise ValueError("Research claim already attempted; reconcile before retrying")
    with ResearchAPI(settings) as api:
        row = api.get(record)
        if (
            row["fields"].get("Status") != "Proposed"
            or row["fields"] != topic["research_source_fields"]
        ):
            raise ValueError("Research topic changed before claiming; reselect it")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        receipt.write_text(
            json.dumps({"state": "claiming", "record": record, "claimed_at": timestamp})
        )
        api.patch(record, {"Status": "Claimed", "Claimed at": timestamp})
        topic["research_claimed_at"] = timestamp
        receipt.write_text(
            json.dumps({"state": "claimed", "record": record, "claimed_at": timestamp})
        )


def transition(
    settings: Settings,
    topic: dict[str, Any],
    status: str,
    *,
    note: str = "",
    url: str = "",
    hold_reason: str = "",
) -> None:
    """Only transition this run's own claim; never reset published or human-held rows."""
    record = topic.get("research_record_id")
    if not record:
        return
    allowed = {
        "Drafting": {"Claimed"},
        "Review": {"Drafting"},
        "Proposed": {"Claimed", "Drafting"},
        # Repeated quality failures park the topic for Chase instead of looping weekly.
        "Held": {"Claimed", "Drafting"},
        "Rejected": {"Review"},
        "Published": {"Review"},
    }
    if hold_reason and status != "Held":
        raise ValueError("Hold reason is only written when holding a topic")
    if status not in allowed or not topic.get("research_claimed_at"):
        raise ValueError("Invalid or unowned research transition")
    with ResearchAPI(settings) as api:
        row = api.get(record)
        fields = row["fields"]
        if not fields.get("Claimed at") or not same_time(
            fields["Claimed at"], topic["research_claimed_at"]
        ):
            raise ValueError("Research claim ownership changed")
        if fields.get("Status") == status:
            if status == "Published" and fields.get("Published URL") != url:
                raise ValueError("Published research URL differs")
            return
        if fields.get("Status") not in allowed[status]:
            raise ValueError("Research status changed; reconcile before updating")
        update: dict[str, Any] = {"Status": status}
        if note:
            update["Review notes"] = (str(fields.get("Review notes", "")) + "\n" + note).strip()
        if hold_reason:
            update["Hold reason"] = hold_reason
        if status == "Published":
            if not url.startswith("https://geo-insulation.com/blog/"):
                raise ValueError("Expected verified Geo blog URL")
            update.update(
                {
                    "Published URL": url,
                    "Published date": datetime.now(timezone.utc).date().isoformat(),
                }
            )
        api.patch(record, update)


def record_decision(
    settings: Settings, draft: dict[str, Any], status: str, *, note: str = "", url: str = ""
) -> None:
    """Surface final-status sync failures without undoing review or publication."""
    topic = draft.get("topic", {})
    if not topic.get("research_record_id"):
        return
    try:
        transition(settings, topic, status, note=note, url=url)
    except Exception as exc:
        from .weekly import private_alert

        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        receipt = settings.storage_dir / ("research-sync-" + topic["research_record_id"] + ".json")
        receipt.write_text(
            json.dumps(
                {
                    "state": "needs_inspection",
                    "record": topic["research_record_id"],
                    "status": status,
                    "url": url,
                    "error": type(exc).__name__,
                }
            )
        )
        private_alert(
            settings,
            f"The article decision is saved, but its Airtable {status} update needs inspection ({type(exc).__name__}).",
        )
