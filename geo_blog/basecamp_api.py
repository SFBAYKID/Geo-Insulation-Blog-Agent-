"""Bounded Basecamp API access; credentials never follow attachment redirects."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from .settings import Settings


class Basecamp:
    """Refresh OAuth per operation and restrict authenticated requests to this account."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        settings.require(
            "basecamp_client_id",
            "basecamp_client_secret",
            "basecamp_refresh_token",
            "basecamp_account_id",
            "basecamp_project_id",
            "basecamp_todolist_id",
        )
        # HTTPX INFO logs include signed attachment URLs. Keep those out of logs.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self.settings = settings
        self.http = client or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_client = client is None
        self.root = f"https://3.basecampapi.com/{settings.basecamp_account_id}/"
        self.bucket = f"buckets/{settings.basecamp_project_id}/"
        self.headers = {
            "User-Agent": "the shared host Geo blog integration (chase@monarchconnected.com)"
        }

    def __enter__(self) -> Basecamp:
        """Authenticate without exposing token response bodies in errors."""
        response = self.http.post(
            "https://launchpad.37signals.com/authorization/token",
            data={
                "type": "refresh",
                "client_id": self.settings.basecamp_client_id,
                "client_secret": self.settings.basecamp_client_secret.get_secret_value(),
                "refresh_token": self.settings.basecamp_refresh_token.get_secret_value(),
            },
            headers=self.headers,
        )
        if response.status_code != 200:
            self.close()
            raise ValueError(f"Basecamp OAuth failed (HTTP {response.status_code})")
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            self.close()
            raise ValueError("Basecamp OAuth response missing access token")
        self.headers["Authorization"] = "Bearer " + token
        return self

    def close(self) -> None:
        """Close only owned transports; tests may inject a shared mock client."""
        if self.owns_client:
            self.http.close()

    def __exit__(self, *args: Any) -> None:
        self.close()

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Fail closed on off-account pagination or redirects, including POST errors."""
        url = path if path.startswith("https://") else self.root + path
        if not url.startswith(self.root) or urlparse(url).fragment:
            raise ValueError("Basecamp URL outside configured account")
        response = self.http.request(method, url, headers=self.headers, **kwargs)
        if response.status_code not in {200, 201}:
            raise ValueError(f"Basecamp request failed (HTTP {response.status_code})")
        return response

    def get(self, path: str) -> dict[str, Any]:
        """Read one validated object."""
        data = self.request("GET", path).json()
        if not isinstance(data, dict):
            raise ValueError("Expected Basecamp object")
        return data

    def listing(self, path: str) -> list[dict[str, Any]]:
        """Follow account-bound pagination with loop protection."""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        while path:
            if path in seen or len(seen) >= 100:
                raise ValueError("Invalid Basecamp pagination")
            seen.add(path)
            response = self.request("GET", path)
            data = response.json()
            if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
                raise ValueError("Expected Basecamp list")
            rows.extend(data)
            path = response.links.get("next", {}).get("url", "")
        return rows

    def task(self, task_id: int) -> dict[str, Any]:
        """Validate project/list ownership even when the API accepts global task IDs."""
        task = self.get(self.bucket + f"todos/{task_id}.json")
        if (
            task.get("id") != task_id
            or task.get("bucket", {}).get("id") != self.settings.basecamp_project_id
            or task.get("parent", {}).get("id") != self.settings.basecamp_todolist_id
        ):
            raise ValueError("Basecamp task is outside the configured blog list")
        return task

    def comments(self, task_id: int) -> list[dict[str, Any]]:
        """Read all comments in chronological API order."""
        return self.listing(self.bucket + f"recordings/{task_id}/comments.json")

    def reviewer(self) -> dict[str, Any]:
        """Resolve the configured reviewer inside this project, never by ambiguous name."""
        people = self.listing(f"projects/{self.settings.basecamp_project_id}/people.json")
        matches = [p for p in people if p.get("id") == self.settings.basecamp_reviewer_id]
        if len(matches) != 1 or not matches[0].get("attachable_sgid"):
            raise ValueError("Basecamp reviewer is unavailable in this project")
        return matches[0]
