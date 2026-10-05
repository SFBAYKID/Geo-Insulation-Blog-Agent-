"""Prompt privately, verify Airtable access, and save only a working token."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import dotenv_values, set_key


def main() -> None:
    """Parse explicit command options and run the requested operation."""
    if not sys.stdin.isatty():
        raise SystemExit("Run this command directly in your terminal for hidden input.")
    env = Path(__file__).resolve().parents[1] / ".env.local"
    config = dotenv_values(env)
    base = config.get("AIRTABLE_BASE_ID") or "appGeoFixture"
    table = config.get("AIRTABLE_TABLE_ID") or "tblGeoFixture"
    token = getpass.getpass("Paste Airtable token (hidden), then press Enter: ").strip()
    if not token or any(c.isspace() for c in token):
        raise SystemExit("No valid token entered. Existing settings were not changed.")
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(
                "https://api.airtable.com/v0/" + quote(base, safe="") + "/" + quote(table, safe=""),
                headers={"Authorization": "Bearer " + token},
                params={"maxRecords": 1},
            )
    except httpx.HTTPError:
        raise SystemExit("Could not reach Airtable. Nothing saved; try again.") from None
    if response.status_code != 200:
        raise SystemExit(
            f"Airtable returned HTTP {response.status_code}. Check the token, data.records:read scope and Geo Insulation Blog base access. Nothing saved."
        )
    if not env.exists():
        env.touch(mode=0o600)
    os.chmod(env, 0o600)
    set_key(str(env), "AIRTABLE_TOKEN", token)
    os.chmod(env, 0o600)
    print("Success: Airtable connection verified and token saved locally.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit("\nCancelled. Nothing saved.") from None
