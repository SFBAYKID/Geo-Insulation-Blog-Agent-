"""Explicit task-to-photo relevance, separate from the image agent's approval catalog.

Basecamp IDs must not be repurposed as Airtable keyword IDs. This small local
mapping pins the task's keyword and URL before returning candidate image IDs;
media_catalog still enforces permission, privacy, checksum, and dimensions.
"""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskMatch(BaseModel):
    """A reviewed relevance association, invalidated by a changed task target."""

    model_config = ConfigDict(extra="forbid")
    task_id: int = Field(gt=0)
    keyword: str = Field(min_length=1)
    planned_url: str = Field(pattern=r"^/blog/[a-z0-9-]+$")
    image_ids: list[str] = Field(min_length=1, max_length=3)


class TaskMatches(BaseModel):
    """Versioned consumer mapping; it cannot grant publication permission."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    tasks: list[TaskMatch]


def candidate_image_ids(topic: dict[str, Any], path: Path) -> set[str]:
    """Return only exact task/keyword/URL matches; never infer relevance from a tag."""
    if not topic.get("basecamp_task_id") or not path.exists():
        return set()
    mapping = TaskMatches.model_validate_json(path.read_text())
    ids = [task.task_id for task in mapping.tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate task photo mappings require inspection")
    for task in mapping.tasks:
        if task.task_id == topic["basecamp_task_id"]:
            if task.keyword != topic.get("keyword") or task.planned_url != topic.get("planned_url"):
                raise ValueError("Task photo relevance changed; review its mapping")
            return set(task.image_ids)
    return set()
