"""Validate an existing deployed article through the real review gate, without Slack delivery."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from geo_blog.settings import Settings
    from geo_blog.store import Store

import argparse
import json
import platform

from geo_blog.preview_pipeline import wait_deployment
from geo_blog.settings import Settings
from geo_blog.store import Store

parser = argparse.ArgumentParser()
parser.add_argument("--sha", help="Test a new preview commit without changing the real draft")
parser.add_argument("draft_id", help="Explicit saved draft ID to inspect")
args = parser.parse_args()
settings = Settings()
folder = Path("storage/server-smoke").resolve()
(folder / "passed.json").unlink(missing_ok=True)
qa = Store(folder / "state")
state = json.loads((settings.storage_dir / args.draft_id / "deployment.json").read_text())
state.pop("scores", None)
if args.sha:
    state["sha"] = args.sha
result = wait_deployment(
    settings,
    qa,
    "smoke",
    state,
    folder / "website",
    folder,
    folder / "hosted-check.log",
)
receipt = json.loads((folder / "hosted-qa.json").read_text())

receipt["host"] = platform.node()
(folder / "passed.json").write_text(json.dumps(receipt, indent=2))
print("Hosted preview quality gate passed:", result["scores"])
print("No article, PR, Slack message, or approval was created.")
