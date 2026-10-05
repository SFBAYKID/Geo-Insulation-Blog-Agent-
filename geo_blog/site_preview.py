"""Build human-review PRs and resolve Vercel previews without merging production.

Each draft has an isolated checkout and durable receipt. Ambiguous pushes or PR
creation are inspected before retrying; this module never force-pushes or merges.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from .settings import Settings
from .site_export import export_post

REPOSITORY = "GEO_WEBSITE_NOT_CONFIGURED"


def preflight(settings: Settings) -> None:
    """Check the live source branch supports previews before spending on prose."""
    if settings.website_repository != REPOSITORY:
        raise ValueError("Geo website preview configuration is required")
    paths = command(
        [
            "gh",
            "api",
            f"repos/{REPOSITORY}/git/trees/{quote(settings.website_base_commit or settings.website_base_branch, safe='')}?recursive=1",
            "--jq",
            ".tree[].path",
        ],
        settings.storage_dir.resolve(),
    ).splitlines()
    required = {
        "geo-web/scripts/check-lighthouse.mjs",
        "geo-web/scripts/prepare-blog-images.mjs",
        "geo-web/src/components/ui/BlogPhoto.tsx",
        ".github/workflows/blog-quality.yml",
    }
    if not required.issubset(paths):
        raise ValueError("Website preview foundation must be merged before generating new blogs")


def command(args: list[str], cwd: Path) -> str:
    """Run a bounded argv command; retain output locally, never print provider secrets."""
    env = dict(os.environ)
    # launchd does not load interactive shell profiles. Include the installed CLI
    # locations so scheduled runs find gh, git's credential helper, and pinned Node.
    locations = [
        Path.home() / ".nvm/versions/node/v22.19.0/bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    env["PATH"] = os.pathsep.join(
        [str(path) for path in locations if path.exists()] + [env.get("PATH", "")]
    )
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=600)
    if result.returncode:
        (cwd / "preview-command-error.log").write_text(result.stdout + "\n" + result.stderr)
        raise RuntimeError(f"Preview command failed: {args[0]}; inspect local error log")
    return result.stdout.strip()


def deployment_url(checkout: Path, sha: str) -> str:
    """Wait for a successful Preview deployment of this exact reviewed Git commit."""
    for _ in range(60):
        deployments = json.loads(
            command(["gh", "api", f"repos/{REPOSITORY}/deployments?sha={sha}"], checkout)
        )
        for deployment in deployments:
            if deployment["environment"].lower() != "preview":
                continue
            statuses = json.loads(
                command(
                    ["gh", "api", f"repos/{REPOSITORY}/deployments/{deployment['id']}/statuses"],
                    checkout,
                )
            )
            if statuses and statuses[0]["state"] == "success":
                url = statuses[0].get("environment_url", "")
                parsed = urlparse(url)
                if parsed.scheme == "https" and (parsed.hostname or "").endswith(".vercel.app"):
                    return url.rstrip("/")
            if statuses and statuses[0]["state"] in {"error", "failure"}:
                raise RuntimeError("Vercel preview deployment failed")
        time.sleep(10)
    raise RuntimeError("Preview still pending; inspect deployment before retrying")


def check_production_quality(checkout: Path, slug: str) -> dict[str, Any]:
    """Block preview delivery until the actual production build meets Lighthouse targets."""
    web = checkout / "geo-web"
    if not (web / "scripts/check-lighthouse.mjs").exists():
        raise ValueError("Website requires the production Lighthouse gate from foundation PR #41")
    command(["env", "VERCEL_ENV=production", "npm", "run", "check"], web)
    command(["npm", "run", "check:lighthouse", "--", "/blog/" + slug], web)
    summary = json.loads((web / "lighthouse-audit/summary.json").read_text())
    result = next((r for r in summary["results"] if r["route"] == "/blog/" + slug), None)
    required = {"performance": 90, "accessibility": 100, "best-practices": 100, "seo": 100}
    if (
        summary.get("environment") != "local production build"
        or not result
        or result.get("passed") is not True
        or any(
            not isinstance(result.get("scores", {}).get(key), (int, float))
            or result["scores"][key] < score
            for key, score in required.items()
        )
    ):
        raise ValueError("Production-build Lighthouse scores did not meet the required targets")
    return {"environment": summary["environment"], "scores": result["scores"]}


def require_ci_quality(checkout: Path, sha: str) -> None:
    """Require GitHub's real quality check for the exact commit before review delivery."""
    for _ in range(90):
        response = json.loads(
            command(
                ["gh", "api", f"repos/{REPOSITORY}/commits/{sha}/check-runs?per_page=100"],
                checkout,
            )
        )
        checks = [
            check
            for check in response.get("check_runs", [])
            if check.get("name") == "lighthouse"
            and check.get("app", {}).get("slug") == "github-actions"
            and check.get("head_sha") == sha
        ]
        if checks:
            # GitHub returns newest checks first; a retry supersedes an earlier attempt.
            latest = max(checks, key=lambda check: check["id"])
            if latest.get("status") == "completed":
                if latest.get("conclusion") == "success":
                    return
                raise ValueError(
                    "GitHub blog quality check failed; preview is not ready for review"
                )
        time.sleep(10)
    raise ValueError("GitHub blog quality check is pending or missing; inspect before retrying")


def prepare(settings: Settings, draft: dict[str, Any], draft_id: str) -> dict[str, Any]:
    """Export and check a draft, open a draft PR, then save its immutable preview link."""
    if not settings.website_preview_enabled or settings.website_repository != REPOSITORY:
        raise ValueError("Geo website preview configuration is required")
    from .basecamp_queue import validate_basecamp_draft

    validate_basecamp_draft(draft)
    root = settings.storage_dir.resolve() / draft_id
    receipt = root / "website-preview.json"
    digest = hashlib.sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest()
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if (
            saved.get("preview_url")
            and saved.get("draft_digest") == digest
            and saved.get("production_lighthouse")
            and saved.get("ci_quality_commit") == saved.get("preview_commit")
        ):
            return dict(draft, **saved)
        raise ValueError("Previous preview needs inspection before retrying")
    checkout = root / "website"
    if checkout.exists():
        raise ValueError("Existing preview checkout needs inspection")
    branch = "feat/blog-agent-" + draft_id
    command(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            settings.website_base_branch,
            f"https://github.com/{REPOSITORY}.git",
            str(checkout),
        ],
        root,
    )
    # A stacked playground preview can use a reviewed foundation without merging main.
    if (
        settings.website_base_commit
        and command(["git", "rev-parse", "HEAD"], checkout) != settings.website_base_commit
    ):
        raise ValueError("Website preview base changed; inspect before retrying")
    command(["git", "switch", "-c", branch], checkout)
    paths = export_post(
        draft, checkout, datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    )
    # Preserve the website's canonical checks; never push an unchecked article.
    quality: dict[str, Any] = {}
    if not settings.website_checks_remote:
        command(["npm", "install", "--ignore-scripts"], checkout / "geo-web")
        quality = check_production_quality(checkout, draft["front_matter"]["slug"])
    paths += ["geo-web/package-lock.json"]
    note = "Blog-agent draft preview; human copy and photo review required. No automatic merge.\n"
    for name in ["README.md", "MEMORY.md"]:
        path = checkout / name
        path.write_text(path.read_text() + "\n## Blog draft " + draft_id + "\n\n" + note)
        paths.append(name)
    doc = checkout / "docs" / ("blog-draft-" + draft_id + ".md")
    doc.write_text(
        "# Blog draft review\n\n"
        + note
        + "\nThe agent exports typed content, checks the site, and opens a draft PR. Approving in Slack is editorial only. Merge only after copy and final media are reviewed.\n"
    )
    paths.append(str(doc.relative_to(checkout)))
    command(["git", "add", "--", *paths], checkout)
    command(["git", "commit", "-m", "feat(blog): prepare article for preview review"], checkout)
    sha = command(["git", "rev-parse", "HEAD"], checkout)
    receipt.write_text(json.dumps({"branch": branch, "preview_commit": sha, "state": "pushing"}))
    command(["git", "push", "-u", "origin", branch], checkout)
    body = root / "pr-body.md"
    body.write_text(
        "Adds a blog draft to the real Geo layout for Slack review.\n\nValidation: website checks must pass before review delivery.\n\nDeferred: final imagery, human editorial approval and production publication. Do not merge a placeholder image.\n"
    )
    pr = command(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--base",
            settings.website_base_branch,
            "--head",
            branch,
            "--title",
            draft["front_matter"]["title"],
            "--body-file",
            str(body),
        ],
        checkout,
    )
    saved = {
        "branch": branch,
        "base_branch": settings.website_base_branch,
        "base_commit": settings.website_base_commit,
        "preview_commit": sha,
        "pr_url": pr,
        "state": "awaiting_preview",
        "production_lighthouse": quality,
        "draft_digest": digest,
    }
    receipt.write_text(json.dumps(saved))
    url = deployment_url(checkout, sha)
    require_ci_quality(checkout, sha)
    if settings.website_checks_remote:
        from .remote_quality import artifact_quality

        saved["production_lighthouse"] = artifact_quality(
            checkout, sha, draft["front_matter"]["slug"]
        )
    saved.update(
        preview_url=url + "/blog/" + draft["front_matter"]["slug"],
        state="ready",
        ci_quality_commit=sha,
    )
    receipt.write_text(json.dumps(saved))
    return dict(draft, **saved)
