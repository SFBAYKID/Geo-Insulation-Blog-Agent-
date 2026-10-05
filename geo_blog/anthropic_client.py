"""Construct Claude clients consistently for workspace-scoped and organization keys."""

from __future__ import annotations

from anthropic import Anthropic

from .settings import Settings


def make_client(settings: Settings, timeout: float = 600) -> Anthropic:
    """Attach the workspace header only when the account requires it."""
    headers = (
        {"anthropic-workspace-id": settings.anthropic_workspace_id}
        if settings.anthropic_workspace_id
        else {}
    )
    return Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        default_headers=headers,
        timeout=timeout,
        max_retries=0,
    )
