"""Run the owner-requested disposable Airtable lifecycle test without paid drafting."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geo_blog.research_api import POSTS, ResearchAPI
from geo_blog.settings import Settings


def digest(rows: list[dict[str, Any]]) -> str:
    """Compare every original source record without putting its content in the report."""
    return hashlib.sha256(
        json.dumps(sorted(rows, key=lambda r: r["id"]), sort_keys=True).encode()
    ).hexdigest()


def run(settings: Settings, receipt: Path) -> dict[str, Any]:
    """Create only the named test row, claim/reject it, delete it and verify originals."""
    settings.require("airtable_write_enabled")
    if receipt.exists():
        raise ValueError("Test already attempted; inspect its receipt rather than repeat")
    with ResearchAPI(settings) as api:
        before = api.rows()
        if any(r["fields"].get("Primary keyword") == "test row, ignore" for r in before):
            raise ValueError("An existing test row needs reconciliation first")
        state: dict[str, Any] = {
            "state": "creating",
            "before_count": len(before),
            "before_hash": digest(before),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(state, indent=2))
        created = api.request(
            "POST",
            POSTS,
            json={"fields": {"Primary keyword": "test row, ignore", "Status": "Proposed"}},
        )
        record = created["id"]
        state.update(record_id=record, state="created")
        receipt.write_text(json.dumps(state, indent=2))
        try:
            current = api.get(record)
            if (
                current["fields"].get("Status") != "Proposed"
                or current["fields"].get("Primary keyword") != "test row, ignore"
            ):
                raise ValueError("Test row changed before claim")
            api.patch(
                record,
                {
                    "Status": "Claimed",
                    "Claimed at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                },
            )
            api.patch(
                record,
                {
                    "Status": "Rejected",
                    "Review notes": "Disposable owner-authorized integration test; no article was generated.",
                },
            )
            state["lifecycle_verified"] = True
        finally:
            # Never delete an unrelated row, including when a human changed its identity.
            current = api.get(record)
            if current["fields"].get("Primary keyword") != "test row, ignore":
                raise ValueError("Test identity changed; inspect before deletion")
            deleted = api.request("DELETE", POSTS, record)
            if deleted.get("id") != record or deleted.get("deleted") is not True:
                raise ValueError("Test deletion requires inspection")
            state["deleted"] = True
            receipt.write_text(json.dumps(state, indent=2))
        after = api.rows()
        state.update(after_count=len(after), after_hash=digest(after))
        state["original_records_unchanged"] = state["before_hash"] == state["after_hash"]
        state["state"] = "passed" if state["original_records_unchanged"] else "needs_inspection"
        receipt.write_text(json.dumps(state, indent=2))
        return state


def main() -> None:
    """Require explicit private environment and receipt paths for each authorized run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file)
    print(json.dumps(run(settings, args.receipt), indent=2))


if __name__ == "__main__":
    main()
