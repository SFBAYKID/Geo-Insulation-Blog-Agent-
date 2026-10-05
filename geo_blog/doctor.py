"""Report selected-workflow configuration without network calls or secret values."""

from .settings import Settings
from .site_preview import REPOSITORY


def report_readiness(settings: Settings) -> bool:
    """Return false while required Geo connections or website integration are missing."""
    keys = [
        "anthropic_api_key",
        "slack_bot_token",
        "slack_app_token",
        "slack_team_id",
        "slack_approver_ids",
    ]
    if settings.blog_queue_source == "basecamp":
        keys += [
            "basecamp_client_id",
            "basecamp_client_secret",
            "basecamp_refresh_token",
            "basecamp_account_id",
            "basecamp_project_id",
            "basecamp_todolist_id",
        ]
    else:
        keys += ["airtable_token", "airtable_base_id"]
    if settings.image_generation_enabled:
        keys += ["openai_api_key"]
    ready = True
    for key in keys:
        value = getattr(settings, key)
        present = bool(value.get_secret_value() if hasattr(value, "get_secret_value") else value)
        print(key.upper() + ": " + ("configured" if present else "missing"))
        ready = ready and present
    print("Test channel:", settings.slack_test_channel_id)
    print("Local listener enabled:", settings.slack_listener_enabled)
    print("Production delivery enabled:", settings.production_delivery_enabled)
    print("Weekly schedule: Thursday 07:00 America/Los_Angeles; installation not verified")
    integration_ready = bool(
        settings.website_preview_enabled
        and settings.website_repository == REPOSITORY
        and "/" in REPOSITORY
    )
    print(
        "Website integration:",
        "configured; live verification still required"
        if integration_ready
        else "pending Astro repository and adapter verification",
    )
    # Provider presence is not evidence that the full website contract works.
    print(
        "Readiness:",
        "configuration present; run live checks" if ready and integration_ready else "incomplete",
    )
    return ready and integration_ready
