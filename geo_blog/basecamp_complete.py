"""Record a verified live blog on its Basecamp task, then mark that task complete.

the client's rule (September 24, 2026): after approval and verified publication, add the
live URL to the blog's Basecamp task and complete it. This runs only after the live
article passed verification, and is idempotent: a restart finds the existing URL
comment and the completed flag instead of posting twice.
"""

from __future__ import annotations

import html
from typing import Any

from .basecamp_api import Basecamp
from .settings import Settings


def record_live(settings: Settings, draft: dict[str, Any], live_url: str) -> str:
    """Return "complete", or "skipped" when the draft has no Basecamp task."""
    task_id = draft.get("topic", {}).get("basecamp_task_id")
    if not task_id:
        return "skipped"
    if not live_url.startswith("https://geo-insulation.com/blog/"):
        raise ValueError("Only a verified geo-insulation.com blog URL can complete a task")
    task_id = int(task_id)
    with Basecamp(settings) as api:
        api.task(task_id)  # Validates project/list ownership before any write.
        if not any(live_url in c.get("content", "") for c in api.comments(task_id)):
            link = html.escape(live_url, quote=True)
            api.request(
                "POST",
                api.bucket + f"recordings/{task_id}/comments.json",
                json={
                    "content": f'<div>Published and verified live: <a href="{link}">{link}</a></div>'
                },
            )
        if not api.task(task_id).get("completed"):
            # Basecamp answers 204 No Content, which the generic request helper rejects.
            response = api.http.post(
                api.root + api.bucket + f"todos/{task_id}/completion.json", headers=api.headers
            )
            if response.status_code not in {200, 201, 204}:
                raise ValueError(f"Basecamp completion failed (HTTP {response.status_code})")
        if not api.task(task_id).get("completed"):
            raise ValueError("Basecamp task did not report completion")
    return "complete"
