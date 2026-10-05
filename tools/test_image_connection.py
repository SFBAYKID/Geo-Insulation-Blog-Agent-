"""Check OpenAI credentials without buying an image or sending any source photo."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from geo_blog.settings import Settings

import httpx

from geo_blog.settings import Settings


def main() -> None:
    """Use the read-only models endpoint; photo work belongs to the catalog agent."""
    settings = Settings()
    response = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": "Bearer " + settings.openai_api_key.get_secret_value()},
        timeout=30,
    )
    print("OpenAI connection:", response.status_code)
    if response.status_code != 200:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
