"""Hold research topics the writer repeatedly cannot finish, instead of retrying weekly.

Before November 11, 2026 a topic that failed quality review went straight back to
Proposed and, as the top priority, was picked (and failed) every Wednesday.

Each exhausted-writer run leaves a marker in its draft folder, keyed by the topic's
record and a signature of the inputs that decide whether a draft can pass. Counting
markers, not draft rows, means:
- outages, preview builds and claim conflicts never count (they leave no marker);
- failures from before this module existed never count, because they were caused by
  the since-fixed keyword rules rather than the topic;
- editing the keywords or brief changes the signature and starts the count over;
- a held topic that comes back to Proposed unchanged (by a person or by the research
  agent's Blog Plan sync) is held again at selection, before any model spend.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .research_lifecycle import transition
from .settings import Settings

MAX_QUALITY_FAILURES = 2
MARKER = "quality-failure.json"
# Inputs that decide whether a draft can pass. Lifecycle fields (Status, notes,
# timestamps) and research metrics are deliberately excluded.
SIGNATURE_FIELDS = (
    "Primary keyword",
    "Supporting keywords",
    "Angle",
    "Must include",
    "Working title",
    "Planned URL",
)


def signature(fields: dict[str, Any]) -> str:
    """Stable fingerprint of the drafting inputs, ignoring case and spacing."""
    normalized = {
        k: " ".join(str(fields.get(k) or "").casefold().split()) for k in SIGNATURE_FIELDS
    }
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()


def failure_count(settings: Settings, record_id: str, fields: dict[str, Any]) -> int:
    """Exhausted-writer runs recorded for this record with these exact inputs."""
    wanted = signature(fields)
    count = 0
    for marker in settings.storage_dir.glob("*/" + MARKER):
        try:
            data = json.loads(marker.read_text())
        except (OSError, ValueError):
            continue
        if data.get("record") == record_id and data.get("signature") == wanted:
            count += 1
    return count


def record_failure(settings: Settings, draft_id: str, topic: dict[str, Any], exc: Any) -> None:
    """Leave the marker that later runs and selection count."""
    (settings.storage_dir / draft_id / MARKER).write_text(
        json.dumps(
            {
                "record": topic["research_record_id"],
                "signature": signature(topic["research_source_fields"]),
                "keyword": topic.get("keyword", ""),
                "secondary_keyword": topic.get("secondary_keyword", ""),
                "blockers": list(getattr(exc, "blockers", []))[:8],
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        )
    )


def hold_reason(topic: dict[str, Any], failures: int, blockers: list[str]) -> str:
    """What Chase sees in Airtable: what failed and how to release the topic."""
    stamp = datetime.now(timezone.utc).date().isoformat()
    return (
        f"{stamp}: the writer failed quality review on {failures} separate runs with "
        f'primary "{topic.get("keyword", "")}" and supporting '
        f'"{topic.get("secondary_keyword", "")}".'
        + (" Last failures: " + "; ".join(blockers[:5]) + "." if blockers else "")
        + " Edit the keywords or brief, then set Status back to Proposed;"
        " re-proposing it unchanged will hold it again."
    )


def release_failed_claim(
    settings: Settings, store: Any, topic: dict[str, Any], draft_id: str, exc: BaseException
) -> str:
    """Return a failed topic to Proposed, or hold it after repeated quality failures.

    Returns the new Airtable status. The local draft stays 'failed_requeued' either
    way, so a topic Chase later re-proposes is selectable again.
    """
    from .writer_feedback import DraftQualityError

    name = type(exc).__name__
    failures = 0
    if isinstance(exc, DraftQualityError):
        record_failure(settings, draft_id, topic, exc)
        failures = failure_count(
            settings, topic["research_record_id"], topic["research_source_fields"]
        )
    if failures >= MAX_QUALITY_FAILURES:
        transition(
            settings,
            topic,
            "Held",
            note=f"Draft stopped ({name}); held after {failures} quality failures.",
            hold_reason=hold_reason(topic, failures, list(getattr(exc, "blockers", []))),
        )
        status = "Held"
    else:
        transition(
            settings,
            topic,
            "Proposed",
            note=f"Draft stopped ({name}); returned for a later attempt.",
        )
        status = "Proposed"
    with store.db() as db:
        db.execute("UPDATE drafts SET status='failed_requeued' WHERE id=?", (draft_id,))
    return status


def last_blockers(settings: Settings, record_id: str, fields: dict[str, Any]) -> list[str]:
    """Most recent recorded failures for a re-hold message."""
    wanted = signature(fields)
    latest: tuple[str, list[str]] = ("", [])
    for marker in settings.storage_dir.glob("*/" + MARKER):
        try:
            data = json.loads(marker.read_text())
        except (OSError, ValueError):
            continue
        if data.get("record") == record_id and data.get("signature") == wanted:
            if str(data.get("recorded_at", "")) >= latest[0]:
                latest = (str(data.get("recorded_at", "")), list(data.get("blockers", [])))
    return latest[1]
