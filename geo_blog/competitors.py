"""Reproducible competitor evidence; model prose never supplies measured values."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings
    from .store import Store
import hashlib
import ipaddress
import json
import re
import socket
import subprocess
from datetime import datetime, timezone
from urllib.parse import urldefrag, urlparse

import httpx
from bs4 import BeautifulSoup

from .audit_config import cpu_args
from .slack_guard import safe_client
from .text_format import count_keyword_in_text

METHOD = (
    "First three distinct competing pages in Google organic web results. Ads, AI Overview citations, forums and video modules excluded. "
    "Word/phrase counts use extracted HTML article/main text (body fallback), excluding navigation, footer, scripts and hidden markup; "
    "JavaScript-only text may be absent. Phrase matching is case-insensitive, whole-phrase and whitespace-normalized. "
    "Lighthouse: one mobile simulated lab run per page, raw scores out of 100; not field data. Scores vary. "
    "Counts are comparison evidence, not keyword-density targets."
)


def utcnow() -> Any:
    """Return an aware UTC timestamp for saved research observations."""
    return datetime.now(timezone.utc).isoformat()


def public_url(url: str) -> Any:
    """Reject private or unsupported addresses before fetching competitor content."""
    p = urlparse(url)
    if (
        p.scheme not in {"https", "http"}
        or not p.hostname
        or p.username
        or p.password
        or p.port not in {None, 80, 443}
    ):
        raise ValueError("Invalid public URL")
    for entry in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80)):
        if not ipaddress.ip_address(entry[4][0]).is_global:
            raise ValueError("Non-public address")
    return urldefrag(url)[0]


def pick_results(raw: dict[str, Any], keyword: str) -> Any:
    """Accept only a saved Google observation, never an LLM search result list."""
    if (
        raw.get("source") not in {"google_browser", "google_serpapi"}
        or raw.get("query", "").casefold() != keyword.strip().casefold()
    ):
        raise ValueError("Google verification required")
    checked = datetime.fromisoformat(raw["checked_at"])
    age = (datetime.now(timezone.utc) - checked).total_seconds()
    if not 0 <= age <= 86400:
        raise ValueError("Google observation is stale")
    if not raw.get("search_url", "").startswith("https://www.google.com/search?"):
        raise ValueError("Missing Google search evidence")
    seen, selected = set(), []
    for item in sorted(raw["results"], key=lambda x: x["position"]):
        if item.get("kind") not in {"organic_article", "organic_page"}:
            continue
        url = urldefrag(item["url"])[0]
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not host or host in seen or host in {"geo-insulation.com", "unrelated.example"}:
            continue
        seen.add(host)
        selected.append(
            {
                "title": item["title"],
                "url": url,
                "result_position": item["position"],
                "query": keyword,
            }
        )
        if len(selected) == 3:
            break
    return selected


def extract_counts(html: Any, primary: Any, secondary: Any) -> Any:
    """Measure article text while excluding navigation, scripts and hidden content."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for node in soup.select(
        'script,style,noscript,svg,nav,footer,aside,[hidden],[aria-hidden="true"]'
    ):
        node.decompose()
    for node in soup.find_all("header"):
        if not node.find_parent(["article", "main"]):
            node.decompose()
    body = soup.body or soup
    candidates = soup.find_all(["article", "main"])
    root = max(
        candidates,
        key=lambda e: (len(e.get_text(" ", strip=True).split()), e.name == "article"),
        default=body,
    )
    # Marketing pages sometimes use article for tiny cards or main only for the hero.
    if len(root.get_text(" ", strip=True).split()) < max(
        80, len(body.get_text(" ", strip=True).split()) * 0.5
    ):
        root = body
    text = re.sub(r"\s+", " ", root.get_text(" ", strip=True)).strip()
    if len(text.split()) < 80 or re.search(
        r"just a moment|verify you are human|access denied|captcha", title, re.I
    ):
        raise ValueError("Blocked, empty or insufficient extractable content")
    return {
        "words": len(text.split()),
        "primary_mentions": count_keyword_in_text(text, primary),
        "secondary_mentions": count_keyword_in_text(text, secondary),
        "scope": root.name,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }, text


def fetch_counts(url: str, primary: Any, secondary: Any, output: Path, client: Any = None) -> Any:
    """Fetch one permitted page and retain the text behind its measured counts."""
    client = client or httpx.Client(
        timeout=35,
        headers={"User-Agent": "GeoBlogResearch/1.0"},
        follow_redirects=False,
    )
    for _ in range(6):
        url = public_url(url)
        response = client.get(url)
        if response.is_redirect:
            from urllib.parse import urljoin

            url = urljoin(url, response.headers["location"])
            continue
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", ""):
            raise ValueError("Not an HTML page")
        counts, text = extract_counts(response.text, primary, secondary)
        output.write_text(text)
        return dict(counts, final_url=url, measured_at=utcnow())
    raise ValueError("Too many redirects")


def lighthouse(url: str, output: Path, settings: Settings | None = None) -> Any:
    """Run an isolated mobile audit and retain its measurement output."""
    public_url(url)
    if settings and settings.pagespeed_api_key.get_secret_value():
        from .pagespeed import run

        raw = run(settings, url, output)
    else:
        binary = Path(__file__).resolve().parents[1] / "node_modules/.bin/lighthouse"
        subprocess.run(
            [
                str(binary),
                url,
                "--chrome-flags=--headless=new",
                *cpu_args(),
                "--only-categories=performance,accessibility,best-practices,seo",
                "--output=json",
                "--output-path=" + str(output.resolve()),
                "--quiet",
            ],
            timeout=150,
            check=True,
            capture_output=True,
            text=True,
        )
        raw = json.loads(output.read_text())
    if (
        raw.get("runtimeError")
        or raw.get("audits", {}).get("http-status-code", {}).get("score") == 0
    ):
        raise ValueError("Lighthouse could not audit the target page")
    return {
        "scores": {
            k: round(v["score"] * 100) if v.get("score") is not None else None
            for k, v in raw["categories"].items()
        },
        "version": raw["lighthouseVersion"],
        "measured_at": raw["fetchTime"],
        "final_url": raw.get("finalDisplayedUrl", raw.get("finalUrl")),
        "warnings": raw.get("runWarnings", []),
    }


def collect(
    settings: Settings,
    topic: dict[str, Any],
    output_dir: Path,
    search: Any = None,
    measure: Any = fetch_counts,
    audit: Any = None,
) -> Any:
    """Collect."""
    if audit is None:

        def audit(url: str, output: Path) -> Any:
            return lighthouse(url, output, settings)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "keyword": topic["keyword"],
        "secondary_keyword": topic.get("secondary_keyword", ""),
        "searched_at": utcnow(),
        "method": METHOD,
        "pages": [],
    }
    try:
        if search is None:
            if settings.google_search_mode == "serpapi":
                from .google_search import google_results

                raw = google_results(settings, topic["keyword"])
                (output_dir / "google-search.json").write_text(json.dumps(raw, indent=2))
            else:
                raw = json.loads((output_dir / "google-search.json").read_text())
        else:
            raw = search(topic["keyword"])
        pages = pick_results(raw, topic["keyword"])
        report.update(
            source=raw["source"],
            searched_at=raw["checked_at"],
            search_url=raw.get("search_url"),
            location=raw.get("location", "United States"),
        )
        (output_dir / "search.json").write_text(json.dumps(raw, indent=2))
        if len(pages) < 3:
            report["search_error"] = (
                f"Only {len(pages)} eligible results returned for the exact query; no substitutes invented."
            )
        for i, page in enumerate(pages, 1):
            page = dict(page)
            try:
                page["content"] = measure(
                    page["url"],
                    topic["keyword"],
                    topic.get("secondary_keyword", ""),
                    output_dir / f"page-{i}.txt",
                )
            except Exception as exc:
                page["content_error"] = (
                    type(exc).__name__ + ": content unavailable or blocked; no count claimed"
                )
            try:
                page["lighthouse"] = audit(
                    page.get("content", {}).get("final_url", page["url"]),
                    output_dir / f"lighthouse-{i}.json",
                )
            except Exception as exc:
                page["lighthouse_error"] = (
                    type(exc).__name__ + ": audit unavailable; no score claimed"
                )
            report["pages"].append(page)
            (output_dir / "report.json").write_text(json.dumps(report, indent=2))
    except Exception as exc:
        report["search_error"] = type(exc).__name__ + ": search unavailable; no ranking claimed"
    report["complete"] = True
    (output_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report


def slack_text(report: dict[str, Any], timezone_name: str = "America/Los_Angeles") -> str:
    """Format measured evidence without inventing missing rankings or scores."""
    from html import escape
    from zoneinfo import ZoneInfo

    def esc(v: Any) -> str:
        return escape(str(v), quote=False)

    try:
        checked = datetime.fromisoformat(report["searched_at"].replace("Z", "+00:00")).astimezone(
            ZoneInfo(timezone_name)
        )
        when = checked.strftime("%B %-d, %Y at %-I:%M %p %Z")
    except (ValueError, KeyError):
        when = "Date unavailable"
    lines = [
        "*Competitor comparison — search snapshot*",
        f"Google checked: {when}",
        f"Search: “{esc(report['keyword'])}”",
    ]
    for i, page in enumerate(report["pages"], 1):
        lines.append(f"\n*{i}. <{page['url']}|{esc(page['title'])[:180]}>*")
        if page.get("result_position"):
            lines.append(f"Google organic position in this search: #{page['result_position']}")
        c = page.get("content")
        if c:
            lines.extend(
                [
                    f"Page length: about {c['words']:,} words",
                    f"“{esc(report['keyword'])}” appears {c['primary_mentions']} times",
                    f"“{esc(report['secondary_keyword'])}” appears {c['secondary_mentions']} times",
                ]
            )
        else:
            lines.append(
                "We couldn’t read this page reliably enough to count its words or keywords."
            )
        lh = page.get("lighthouse")
        if lh:
            scores = lh["scores"]
            lines.append(
                "Mobile website scores (out of 100): "
                + " · ".join(
                    f"{name}: {scores.get(key) if scores.get(key) is not None else 'Unavailable'}"
                    for name, key in [
                        ("Speed", "performance"),
                        ("Accessibility", "accessibility"),
                        ("Best practices", "best-practices"),
                        ("SEO", "seo"),
                    ]
                )
            )
        else:
            lines.append("We couldn’t complete the mobile website test for this page.")
    if report.get("search_error"):
        lines.append(
            "\nThe competitor search could not be completed reliably. No unverified rankings are claimed."
        )
    if report.get("source") in {"google_browser", "google_serpapi"}:
        source = (
            "Google results supplied by SerpApi"
            if report["source"] == "google_serpapi"
            else "Google results checked in the browser"
        )
        lines.append(
            "\n"
            + source
            + ". First three eligible competing pages in this dated search, excluding ads, forums and video results. Desktop search location: "
            + esc(report.get("location", "United States"))
            + ". This is a snapshot, not a guarantee of what appears in your current search. Word counts are approximate; mobile test scores can change."
        )
        if report.get("search_url", "").startswith("https://www.google.com/search?"):
            lines.append("<" + report["search_url"] + "|Open a current Google search>")
    return "\n".join(lines)


def update_thread(
    settings: Settings,
    store: Store,
    draft_id: str,
    topic: dict[str, Any] | None = None,
    client: Any = None,
    collector: Any = collect,
    *,
    refresh: bool = False,
) -> Any:
    """Edit the bot's opening message; keep the approval card and article immutable."""
    from .slack_app import topic_message

    row = store.get(draft_id)
    if not row or not row.get("thread_ts") or row["channel"] != settings.slack_channel_id:
        raise ValueError("Competitor evidence requires the saved blog thread")
    topic = topic or json.loads(row["payload"])["topic"]
    client = safe_client(settings, client)
    if client.auth_test()["team_id"] != settings.slack_team_id:
        raise ValueError("Wrong Slack workspace")
    with store.db() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS competitor_reports (draft_id TEXT PRIMARY KEY, report TEXT, upload_state TEXT, file_url TEXT, updated INTEGER DEFAULT 0)"
        )
        db.execute("INSERT OR IGNORE INTO competitor_reports(draft_id) VALUES(?)", (draft_id,))
        saved = dict(
            db.execute("SELECT * FROM competitor_reports WHERE draft_id=?", (draft_id,)).fetchone()
        )
    keyword_changed = bool(
        saved["report"]
        and any(
            json.loads(saved["report"]).get(k, "") != topic.get(k, "")
            for k in ("keyword", "secondary_keyword")
        )
    )
    if (
        not refresh
        and not keyword_changed
        and saved["updated"]
        and json.loads(saved["report"]).get("source") in {"google_browser", "google_serpapi"}
    ):
        return json.loads(saved["report"])
    folder = settings.storage_dir / draft_id / "competitors"
    if refresh:
        folder.mkdir(parents=True, exist_ok=True)
        if saved["report"]:
            archive = folder / "history"
            archive.mkdir(exist_ok=True)
            (
                archive / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
            ).write_text(saved["report"])
        report = collector(settings, topic, folder)
        if report.get("search_error") or len(report.get("pages", [])) != 3:
            raise ValueError(
                "Refresh did not verify three competitors; existing Slack comparison preserved"
            )
    elif saved["report"] and json.loads(saved["report"]).get("source") in {
        "google_browser",
        "google_serpapi",
    }:
        report = json.loads(saved["report"])
    elif (folder / "report.json").exists():
        report = json.loads((folder / "report.json").read_text())
        # Interrupted partial runs must finish, not silently look complete.
        if not report.get("complete") or report.get("source") not in {
            "google_browser",
            "google_serpapi",
        }:
            report = collector(settings, topic, folder)
    else:
        report = collector(settings, topic, folder)
    if not refresh and keyword_changed:
        if report["keyword"] != topic["keyword"]:
            raise ValueError("A changed primary keyword requires fresh Google verification")
        archive = folder / "history"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")).write_text(
            json.dumps(report, indent=2)
        )
        report["secondary_keyword"] = topic.get("secondary_keyword", "")
        for i, page in enumerate(report["pages"], 1):
            if not page.get("content"):
                continue
            # Recount the exact saved text. Preserve the dated search and raw speed tests.
            text = (folder / f"page-{i}.txt").read_text()
            if hashlib.sha256(text.encode()).hexdigest() != page["content"]["text_sha256"]:
                raise ValueError("Saved competitor text changed; a fresh measurement is required")
            page["content"]["secondary_mentions"] = count_keyword_in_text(
                text, report["secondary_keyword"]
            )
        (folder / "report.json").write_text(json.dumps(report, indent=2))
    with store.db() as db:
        db.execute(
            "UPDATE competitor_reports SET report=?,updated=0 WHERE draft_id=?",
            (json.dumps(report), draft_id),
        )
    # Idempotent update of the known bot-owned root. Never post another channel message.
    client.chat_update(
        channel=row["channel"],
        ts=row["thread_ts"],
        text=topic_message(topic) + "\n\n" + slack_text(report, settings.timezone),
        blocks=[],
        unfurl_links=False,
        unfurl_media=False,
    )
    with store.db() as db:
        db.execute("UPDATE competitor_reports SET updated=1 WHERE draft_id=?", (draft_id,))
    return report
