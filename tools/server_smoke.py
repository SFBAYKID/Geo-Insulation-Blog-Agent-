"""Read-only connection checks; no drafts, Slack messages, image purchases or deploys."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from geo_blog.settings import Settings

import httpx
from slack_sdk import WebClient

from geo_blog.settings import Settings
from geo_blog.topics import select_topic


def main() -> None:
    """Report connection outcomes without printing credentials or provider bodies."""
    settings = Settings()
    checks = {
        "Slack workspace": lambda: (
            WebClient(token=settings.slack_bot_token.get_secret_value()).auth_test()["team_id"]
            == settings.slack_team_id
        ),
        "Airtable keyword read": lambda: bool(select_topic(settings, set())),
        "OpenAI model access": lambda: (
            httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": "Bearer " + settings.openai_api_key.get_secret_value()},
                timeout=30,
            ).status_code
            == 200
        ),
    }
    failed = False
    for name, check in checks.items():
        try:
            passed = check()
            print(name + ": " + ("PASS" if passed else "FAIL"))
            failed |= not passed
        except Exception as error:
            failed = True
            print(name + ": " + type(error).__name__)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
