"""Read exact-commit GitHub build artifacts so the small Droplet need not run Chrome."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .site_preview import REPOSITORY, command


def artifact_quality(checkout: Path, sha: str, slug: str) -> dict[str, Any]:
    """Require a successful matching workflow plus its unmodified Lighthouse summary."""
    runs = json.loads(
        command(
            [
                "gh",
                "run",
                "list",
                "--repo",
                REPOSITORY,
                "--commit",
                sha,
                "--workflow",
                "blog-quality.yml",
                "--json",
                "databaseId,headSha,status,conclusion",
            ],
            checkout,
        )
    )
    matches = [r for r in runs if r["headSha"] == sha]
    if not matches:
        raise ValueError("No successful exact-commit quality workflow")
    run = max(matches, key=lambda r: r["databaseId"])
    if run["status"] != "completed" or run["conclusion"] != "success":
        raise ValueError("Latest exact-commit quality workflow did not succeed")
    folder = checkout.parent / ("github-quality-" + sha)
    if not folder.exists():
        command(
            [
                "gh",
                "run",
                "download",
                str(run["databaseId"]),
                "--repo",
                REPOSITORY,
                "--name",
                "production-build-lighthouse",
                "--dir",
                str(folder),
            ],
            checkout,
        )
    summary = json.loads((folder / "summary.json").read_text())
    result = next((r for r in summary["results"] if r["route"] == "/blog/" + slug), None)
    required = {"performance": 90, "accessibility": 100, "best-practices": 100, "seo": 100}
    if (
        summary.get("commit") != sha
        or summary.get("environment") != "local production build"
        or not result
        or not result.get("passed")
        or any(result.get("scores", {}).get(k, 0) < v for k, v in required.items())
    ):
        raise ValueError("GitHub article Lighthouse result failed")
    return {
        "environment": "GitHub production build",
        "scores": result["scores"],
        "workflow_run": run["databaseId"],
        "commit": sha,
    }
