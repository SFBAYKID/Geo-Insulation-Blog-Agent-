"""Local exports preserve review gates, escape content and never publish."""

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from geo_blog.content import parse_draft
from geo_blog.local_preview import export_preview, inline_html
from geo_blog.model_usage import fingerprint


@pytest.fixture
def approved_payload(tmp_path, monkeypatch, valid_article):
    monkeypatch.chdir(tmp_path)
    image = Path("assets/test.webp")
    image.parent.mkdir()
    image.write_bytes(
        b"RIFF"
        + (22).to_bytes(4, "little")
        + b"WEBPVP8X"
        + (10).to_bytes(4, "little")
        + bytes(4)
        + (1535).to_bytes(3, "little")
        + (863).to_bytes(3, "little")
    )
    text = (
        re.sub(r"\n(## [^\n]+)\n", r"\n\n\1\n\n", valid_article)
        + "\n\n## Frequently Asked Questions\n\n**What is the first step?**\n\nDiscuss the existing conditions.\n"
    )
    draft = {
        "markdown": text,
        "front_matter": parse_draft(text)[0],
        "sources": [
            "https://geo-insulation.com/",
            "https://example.com/docs",
            "https://example.org/research",
        ],
        "topic": {"keyword": "AI workflow automation"},
    }
    folder = Path("storage/sample")
    folder.mkdir(parents=True)
    (folder / "approved-prose.json").write_text(
        json.dumps({"draft": copy.deepcopy(draft), "draft_sha256": fingerprint(draft)})
    )
    draft["topic"]["hero"] = {
        "alt": "An attic illustration",
        "caption": "Illustration: An attic.",
        "width": 1536,
        "height": 864,
    }
    draft.update(
        media_status="ready",
        media_provenance={
            "origin": "generated",
            "derivative_path": str(image),
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        },
    )
    path = folder / "payload.json"
    path.write_text(json.dumps(draft))
    return path


def test_local_bundle_has_matching_faq_schema_and_no_publication_state(approved_payload):
    result = export_preview(approved_payload)
    html = BeautifulSoup(result.read_text(), "html.parser")
    assert html.select_one("meta[name=robots]")["content"] == "noindex,nofollow"
    assert len(html.select("h1")) == 1
    assert html.select_one(".review-bar strong").text == "LOCAL DRAFT · NOT PUBLISHED"
    schema = json.loads(html.select_one('script[type="application/ld+json"]').string)
    assert schema["mainEntity"][0]["name"] == html.select_one(".faq summary").text
    assert (
        schema["mainEntity"][0]["acceptedAnswer"]["text"] == html.select_one(".faq details p").text
    )
    assert not html.select("form, script[src]")
    assert (result.parent / "hero.webp").exists()
    assert (Path("previews/assets") / "bitter-latin.woff2").exists()
    assert Path("previews/index.html").exists()
    portable = json.loads((result.parent / "article.json").read_text())
    assert portable["publication_approved"] is False
    assert "media_provenance" not in portable and "topic" not in portable
    assert export_preview(approved_payload) == result


@pytest.mark.parametrize(
    "change,error",
    [
        ("copy", "changed after editorial approval"),
        ("image", "Image changed"),
        ("caption", "Illustration caption"),
        ("path", "inside the agent assets"),
        ("dimensions", "dimensions"),
    ],
)
def test_tampered_or_unlabeled_draft_is_not_exported(approved_payload, change, error):
    draft = json.loads(approved_payload.read_text())
    if change == "copy":
        draft["markdown"] += "\nChanged."
    elif change == "image":
        Path(draft["media_provenance"]["derivative_path"]).write_bytes(b"changed")
    elif change == "caption":
        draft["topic"]["hero"]["caption"] = "A real customer project"
    elif change == "dimensions":
        draft["topic"]["hero"]["width"] = 100
    else:
        draft["media_provenance"]["derivative_path"] = ".env.local"
    approved_payload.write_text(json.dumps(draft))
    with pytest.raises(ValueError, match=error):
        export_preview(approved_payload)
    assert not Path("previews").exists()


def test_inline_authored_text_cannot_execute_html_or_script_urls():
    assert inline_html(["<script>alert(1)</script>"]) == "&lt;script&gt;alert(1)&lt;/script&gt;"
    with pytest.raises(ValueError, match="Unsafe"):
        inline_html([{"text": "Click", "href": "javascript:alert(1)"}])


def test_saved_local_draft_renders_without_keys_queue_or_generation(approved_payload, monkeypatch):
    import shutil
    from unittest.mock import Mock

    import geo_blog.local_workflow as workflow
    from geo_blog.settings import Settings

    topic = {
        "topic": "Sample",
        "keyword": "AI workflow automation",
        "secondary_keyword": "workflow",
        "angle": "Test",
    }
    brief = Path("brief.json")
    brief.write_text(json.dumps(topic))
    folder = Path("storage/local-drafts") / fingerprint(topic)[:16]
    shutil.copytree(approved_payload.parent, folder)
    writer = Mock(
        side_effect=AssertionError("Saved preview must not generate or contact providers")
    )
    monkeypatch.setattr(workflow, "Writer", writer)
    result = workflow.build_local(Settings(_env_file=None), brief, Path("previews"))
    assert result.exists()
    writer.assert_not_called()


def test_local_generation_requires_complete_brief_before_provider_use(tmp_path):
    from geo_blog.local_workflow import build_local
    from geo_blog.settings import Settings

    path = tmp_path / "brief.json"
    path.write_text('{"topic":"Incomplete"}')
    with pytest.raises(ValueError, match="brief needs"):
        build_local(Settings(_env_file=None), path, tmp_path / "preview")
