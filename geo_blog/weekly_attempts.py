"""Bounded weekly topic fallback and private advisory findings."""

from __future__ import annotations

import uuid
from typing import Any

from .settings import Settings
from .store import Store
from .writer_feedback import DraftQualityError, describe_check


def draft_with_fallback(settings: Settings, store: Store, run_daily: Any) -> str | None:
    """Try at most three distinct topics; only quality failures permit another."""
    from .weekly import private_alert

    excluded: set[str] = set()
    run_id = uuid.uuid4().hex
    for attempt in range(3):
        try:
            result = run_daily(
                settings,
                store,
                send=False,
                request_key=f"weekly:{run_id}:{attempt}",
                excluded_topics=set(excluded),
            )
            return str(result) if result else None
        except DraftQualityError as exc:
            if not exc.topic_id or attempt == 2:
                raise
            excluded.add(exc.topic_id)
            private_alert(
                settings,
                f'Topic "{exc.keyword}" failed quality review. Trying the next topic.\n'
                + "\n".join(exc.blockers[:8]),
            )
    return None


def report_soft_findings(settings: Settings, draft: dict[str, Any]) -> None:
    """Keep advisory SEO findings in the owner's private test channel."""
    from .weekly import private_alert

    findings = [
        describe_check(c)
        for c in draft.get("report", {}).get("checks", [])
        if not c.get("passed") and not c.get("is_hard", True)
    ]
    if findings:
        private_alert(
            settings, "Blog passed hard checks. Advisory SEO findings:\n" + "\n".join(findings)
        )
