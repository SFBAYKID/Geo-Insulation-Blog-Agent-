"""Run standard mobile Lighthouse on Google's service, keeping credentials out of reports."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def page_identity(url: str) -> Any:
    """Normalize the public page identity used to validate an audit response."""
    parts = urlsplit(url)
    return parts.scheme, parts.netloc, parts.path


def run(
    settings: Settings,
    url: str,
    output: Path,
    *,
    protected: bool = False,
    open_url: Any = urlopen,
    sleep: Any = time.sleep,
) -> Any:
    """Run."""
    settings.require("pagespeed_api_key")
    key = settings.pagespeed_api_key.get_secret_value()
    secrets = [key]
    target = url
    if protected:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or not parts.hostname.endswith(".vercel.app")
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Expected a clean protected Vercel preview URL")
        settings.require("vercel_automation_bypass_secret")
        bypass = settings.vercel_automation_bypass_secret.get_secret_value()
        secrets.append(bypass)
        target = urlunsplit(
            parts._replace(
                query=urlencode(
                    {
                        "x-vercel-protection-bypass": bypass,
                        "x-vercel-set-bypass-cookie": "true",
                    }
                )
            )
        )
    else:
        from .competitors import public_url

        public_url(url)
    params = [("url", target), ("strategy", "mobile")]
    params += [
        ("category", name) for name in ("performance", "accessibility", "best-practices", "seo")
    ]
    request = Request(ENDPOINT + "?" + urlencode(params), headers={"X-Goog-Api-Key": key})
    # Google's service has brief outages: an HTTP 500 at 06:42 UTC passed minutes later on
    # the same preview (September 22, 2026). Waiting 2 and 4 seconds did not ride it out.
    for attempt in range(5):
        try:
            with open_url(request, timeout=180) as response:
                raw = response.read(20_000_001)
            if len(raw) > 20_000_000:
                raise RuntimeError("PageSpeed response exceeded the report size limit")
            data = json.loads(raw)
            break
        except HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < 4:
                sleep(15 * 2**attempt)
                continue
            raise RuntimeError(f"PageSpeed service returned HTTP {exc.code}") from None
        except (URLError, TimeoutError):
            if attempt < 4:
                sleep(15 * 2**attempt)
                continue
            raise RuntimeError("PageSpeed service could not be reached") from None
    report = data.get("lighthouseResult")
    if not isinstance(report, dict):
        raise RuntimeError("PageSpeed did not return a Lighthouse report")
    # Google must receive the preview-access URL, but it must never appear in saved
    # evidence, Slack, logs, or PRs. Preserve all scores and measurements unchanged.
    serialized = json.dumps(report)
    for secret in secrets:
        serialized = serialized.replace(secret, "[redacted]")
    report = json.loads(serialized)
    output.write_text(serialized)
    output.chmod(0o600)
    final_url = report.get("finalDisplayedUrl", report.get("finalUrl", ""))
    if (
        report.get("runtimeError")
        or report.get("audits", {}).get("http-status-code", {}).get("score") == 0
    ):
        raise RuntimeError("PageSpeed could not audit the target page")
    if protected and page_identity(final_url) != page_identity(url):
        raise RuntimeError("PageSpeed did not measure the requested article")
    categories = report.get("categories", {})
    if any(
        categories.get(name, {}).get("score") is None
        for name in ("performance", "accessibility", "best-practices", "seo")
    ):
        raise RuntimeError("PageSpeed returned incomplete category scores")
    return report
