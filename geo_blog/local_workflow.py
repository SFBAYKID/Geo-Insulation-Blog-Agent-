"""Generate or resume local drafts without Slack, queue mutations or publishing."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .content import Writer
from .generated_hero import ensure_image
from .local_preview import export_preview
from .media_catalog import attach_media
from .model_usage import fingerprint, workflow_estimate, workflow_estimate_text
from .settings import Settings


def build_local(settings: Settings, brief: Path, destination: Path) -> Path:
    """Retain approved content across retries and export a private design preview."""
    topic = json.loads(brief.read_text())
    if not isinstance(topic, dict) or any(
        not isinstance(topic.get(key), str) or not topic[key].strip()
        for key in ("topic", "keyword", "secondary_keyword", "angle")
    ):
        raise ValueError("Local brief needs topic, keyword, secondary_keyword and angle")
    identity = fingerprint(topic)[:16]
    topic.update(id="local:" + identity, preview_only=True)
    folder = settings.storage_dir / "local-drafts" / identity
    payload = folder / "payload.json"
    if payload.exists():
        return export_preview(payload, destination)
    settings.require("openai_api_key", "image_generation_enabled")
    local = settings.model_copy(
        update={
            "website_preview_enabled": False,
            "production_delivery_enabled": False,
            "slack_listener_enabled": False,
        }
    )
    print(
        workflow_estimate_text(workflow_estimate(local.writer_model, include_visuals=False)),
        flush=True,
    )
    writer = Writer(local)
    try:
        draft = writer.draft(
            topic, datetime.now(ZoneInfo(local.timezone)).date().isoformat(), folder
        )
        (folder / "prose-payload.json").write_text(json.dumps(draft, indent=2))
        draft = ensure_image(local, writer, attach_media(local, draft))
        payload.write_text(json.dumps(draft, indent=2))
    finally:
        writer.client.close()
    return export_preview(payload, destination)
