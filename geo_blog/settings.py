"""Typed configuration; environment values override local files and secrets stay masked."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), extra="ignore", populate_by_name=True
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("OPENAI_API_KEY", "OPEN_AI_API_KEY", "OPEN_AI_KEY"),
    )
    image_model: str = "gpt-image-2.5-flare-2026-09-08"
    image_generation_enabled: bool = False
    google_search_mode: str = "browser"
    serpapi_api_key: SecretStr = SecretStr("")
    google_search_location: str = "San Antonio, Texas, United States"
    # Full corrections are more reliable for small prose models than literal patch JSON.
    writer_patch_corrections: bool = False
    writer_model: str = "gpt-6-luna"
    deployment_target: Literal["local", "droplet"] = "local"
    slack_listener_enabled: bool = False
    slack_bot_token: SecretStr = SecretStr("")
    slack_app_token: SecretStr = SecretStr("")
    slack_team_id: str = ""
    slack_channel_id: str = "C0B02721MNK"
    slack_approver_ids: str = ""
    airtable_token: SecretStr = SecretStr("")
    airtable_base_id: str = ""
    airtable_table: str = Field(
        default="Keywords",
        validation_alias=AliasChoices("AIRTABLE_TABLE_ID", "AIRTABLE_TABLE"),
    )
    airtable_view: str = Field(
        default="", validation_alias=AliasChoices("AIRTABLE_VIEW_ID", "AIRTABLE_VIEW")
    )
    # Basecamp is opt-in for existing installations; never fall back on API failure.
    blog_queue_source: str = "airtable"
    basecamp_client_id: str = ""
    basecamp_client_secret: SecretStr = SecretStr("")
    basecamp_refresh_token: SecretStr = SecretStr("")
    basecamp_account_id: str = ""
    basecamp_project_id: int = 0
    basecamp_todolist_id: int = 0
    basecamp_reviewer_id: int = 0
    basecamp_draft_ready_enabled: bool = False
    airtable_write_enabled: bool = False
    queue_low_threshold: int = Field(default=5, ge=1)
    pagespeed_api_key: SecretStr = SecretStr("")
    vercel_automation_bypass_secret: SecretStr = SecretStr("")
    website_source: Path = Path("website-checkout")
    website_repository: str = ""
    github_token: SecretStr = SecretStr("")
    website_base_commit: str = ""
    website_base_branch: str = "main"
    production_delivery_enabled: bool = False
    publishing_enabled: bool = False
    website_production_branch: str = "main"
    revisions_enabled: bool = False
    production_chat_enabled: bool = False
    daily_enabled: bool = False
    daily_hour: int = Field(default=21, ge=0, le=23)
    timezone: str = "America/Los_Angeles"
    storage_dir: Path = Path("storage")

    # Test isolation is enforced before any outbound Slack operation.
    slack_test_channel_id: str = "C0B02721MNK"
    slack_production_channel_id: str = ""
    slack_signing_secret: SecretStr = SecretStr("")
    runtime_environment: str = "test"
    competitors_enabled: bool = False
    website_preview_enabled: bool = False
    website_checks_remote: bool = True
    image_catalog_path: Path = Path("config/image-catalog.json")
    media_required_for_publish: bool = True
    image_generation_fallback_reason: str = ""

    @model_validator(mode="after")
    def enforce_test_boundary(self) -> Self:
        """Keep ordinary Slack operations isolated and require explicit publication setup."""
        if self.blog_queue_source not in {"airtable", "basecamp"}:
            raise ValueError("Unknown blog queue source")
        if self.basecamp_draft_ready_enabled and (
            self.blog_queue_source != "basecamp"
            or not self.basecamp_reviewer_id
            or not self.website_preview_enabled
        ):
            raise ValueError("Basecamp review needs its queue, reviewer and website preview")
        if self.runtime_environment != "test":
            raise ValueError("Only test runtime is enabled in this release")
        if not self.slack_channel_id or self.slack_channel_id != self.slack_test_channel_id:
            raise ValueError("Active Slack channel must be the test channel")
        if self.slack_test_channel_id == self.slack_production_channel_id:
            raise ValueError("Test and production channels must differ")
        if self.daily_enabled:
            raise ValueError("Daily scheduling is disabled; use the Pacific weekly scheduler")
        if self.publishing_enabled and not (
            self.production_delivery_enabled
            and self.slack_production_channel_id
            and self.approvers
            and self.website_preview_enabled
            and self.website_repository
            and self.website_base_branch == self.website_production_branch == "main"
        ):
            raise ValueError(
                "Publishing requires production destination, reviewers and main previews"
            )
        return self

    @property
    def approvers(self) -> set[str]:
        return {s.strip() for s in self.slack_approver_ids.split(",") if s.strip()}

    def require(self, *names: str) -> None:
        missing = [
            n.upper()
            for n in names
            if not (
                getattr(self, n).get_secret_value()
                if isinstance(getattr(self, n), SecretStr)
                else getattr(self, n)
            )
        ]
        if missing:
            raise ValueError(
                "Configure " + ", ".join(missing) + " in the environment or .env.local"
            )
