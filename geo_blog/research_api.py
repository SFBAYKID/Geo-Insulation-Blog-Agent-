"""Dedicated Geo record access; no schema or research-field mutations are exposed."""

from __future__ import annotations

import re
from typing import Any

import httpx

from .settings import Settings

BASE = "appJnXLPZjVEfvWiL"
POSTS = "tbll5xV6zwbZSMSjR"
COVERAGE = "tbl0Yxt6Dz8nXPsEH"
WRITABLE = {
    "Status",
    "Claimed at",
    "Review notes",
    "Published URL",
    "Published date",
    "Hold reason",
}
STATES = {"Proposed", "Claimed", "Drafting", "Review", "Published", "Rejected", "Held"}


class ResearchAPI:
    """Bound all calls to the configured Geo base and known record tables."""

    def __init__(self, settings: Settings, client: Any = None) -> None:
        settings.require("airtable_token")
        if settings.airtable_base_id != BASE or settings.airtable_table != POSTS:
            raise ValueError("Expected the dedicated Geo Blog Posts configuration")
        self.settings = settings
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.owned = client is None
        self.headers = {"Authorization": "Bearer " + settings.airtable_token.get_secret_value()}

    def __enter__(self) -> ResearchAPI:
        return self

    def __exit__(self, *args: Any) -> None:
        if self.owned:
            self.client.close()

    def request(self, method: str, table: str, record: str = "", **kwargs: Any) -> Any:
        """Validate identifiers and suppress response bodies in failure messages."""
        if table not in {POSTS, COVERAGE} or (
            record and not re.fullmatch(r"rec[A-Za-z0-9]+", record)
        ):
            raise ValueError("Unknown Geo research destination")
        if method != "GET" and table != POSTS:
            raise ValueError("Existing Coverage is read-only")
        response = self.client.request(
            method,
            f"https://api.airtable.com/v0/{BASE}/{table}" + (f"/{record}" if record else ""),
            headers=self.headers,
            **kwargs,
        )
        if response.status_code != 200:
            raise ValueError(f"Geo Airtable {method} failed (HTTP {response.status_code})")
        return response.json()

    def rows(self, table: str = POSTS) -> list[dict[str, Any]]:
        """Read every page without filters that could hide held or published work."""
        rows: list[dict[str, Any]] = []
        params: dict[str, Any] = {"pageSize": 100}
        offsets: set[str] = set()
        while True:
            data = self.request("GET", table, params=params)
            if not isinstance(data, dict) or not isinstance(data.get("records"), list):
                raise ValueError("Invalid Airtable listing")
            for row in data["records"]:
                if not isinstance(row, dict) or not isinstance(row.get("fields"), dict):
                    raise ValueError("Invalid Airtable record")
                if not re.fullmatch(r"rec[A-Za-z0-9]+", str(row.get("id", ""))):
                    raise ValueError("Invalid Airtable record identity")
                rows.append(row)
            offset = data.get("offset")
            if not offset:
                return rows
            if not isinstance(offset, str) or offset in offsets or len(offsets) > 100:
                raise ValueError("Invalid Airtable pagination")
            offsets.add(offset)
            params["offset"] = offset

    def get(self, record: str) -> dict[str, Any]:
        """Reread one source record immediately before a transition."""
        row = self.request("GET", POSTS, record)
        if row.get("id") != record or not isinstance(row.get("fields"), dict):
            raise ValueError("Invalid Airtable record")
        return dict(row)

    def patch(self, record: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Write only lifecycle fields, then verify the requested values by rereading."""
        if not self.settings.airtable_write_enabled:
            raise ValueError("Airtable lifecycle writes are disabled")
        if not fields or set(fields) - WRITABLE or fields.get("Status") not in STATES:
            raise ValueError("Invalid lifecycle update")
        if "Hold reason" in fields and fields["Status"] != "Held":
            raise ValueError("Hold reason is only writable when holding an overlap")
        self.request("PATCH", POSTS, record, json={"fields": fields})
        current = self.get(record)
        for key, value in fields.items():
            actual = current["fields"].get(key)
            if key == "Claimed at" and actual and value:
                from datetime import datetime

                if datetime.fromisoformat(actual.replace("Z", "+00:00")) == datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                ):
                    continue
            if actual != value:
                raise ValueError("Airtable transition requires reconciliation")
        return current
