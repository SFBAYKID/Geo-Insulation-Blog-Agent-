"""Preview conversion and compact Slack regression checks without network calls."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from geo_blog.site_export import blocks, inline
from geo_blog.site_preview import deployment_url
from geo_blog.slack_app import review_blocks


def test_block_conversion_keeps_links_and_lists():
    title, body = blocks(
        "# Heading\n\nText [site](https://example.com).\n\n## Section\n\n- **Lead.** Body"
    )
    assert title == "Heading"
    assert body[0]["content"][1] == {"text": "site", "href": "https://example.com"}
    assert body[2]["items"] == [{"lead": "Lead.", "text": "Body"}]
    with pytest.raises(ValueError):
        blocks("# Heading\n\n<table>unsafe</table>")
    assert inline("**Bold**") == [{"bold": "Bold"}]


def test_preview_requires_exact_commit_and_preview_environment(monkeypatch):
    run = Mock(
        side_effect=[
            json.dumps(
                [{"id": 1, "environment": "Production"}, {"id": 2, "environment": "Preview"}]
            ),
            json.dumps([{"state": "success", "environment_url": "https://exact.vercel.app"}]),
        ]
    )
    monkeypatch.setattr("geo_blog.site_preview.command", run)
    assert deployment_url(Path("."), "exactsha") == "https://exact.vercel.app"
    assert "sha=exactsha" in run.call_args_list[0].args[0][2]
    assert "/deployments/2/statuses" in run.call_args_list[1].args[0][2]


def test_review_card_has_links_and_real_approval_actions():
    draft = {
        "front_matter": {"title": "Title", "description": "Short summary"},
        "report": {"word_count": 1000},
        "topic": {"keyword": "main", "secondary_keyword": "support"},
        "preview_url": "https://exact.vercel.app/blog/test",
        "pr_url": "https://github.com/owner/repo/pull/1",
        "markdown": "FULL ARTICLE MUST NOT APPEAR",
    }
    card = review_blocks(draft, "draft-id")
    encoded = json.dumps(card)
    assert draft["preview_url"] in encoded and draft["pr_url"] in encoded
    assert draft["markdown"] not in encoded
    assert {b["action_id"] for b in card[-1]["elements"]} == {"blog_approve", "blog_reject"}


def test_jpeg_reader_rejects_identifying_metadata():
    from geo_blog.media_catalog import jpeg_size

    frame = b"\xff\xd8\xff\xc0\x00\x0b\x08\x03\x00\x04\x00\x01\x01\x11\x00\xff\xda"
    assert jpeg_size(frame) == (1024, 768)
    private = frame[:2] + b"\xff\xe1\x00\x06Exif" + frame[2:]
    with pytest.raises(ValueError, match="private metadata"):
        jpeg_size(private)


def test_production_quality_gate_blocks_bad_or_wrong_article(tmp_path, monkeypatch):
    from geo_blog.site_preview import check_production_quality

    web = tmp_path / "geo-web"
    (web / "scripts").mkdir(parents=True)
    (web / "scripts/check-lighthouse.mjs").touch()
    (web / "lighthouse-audit").mkdir()
    run = Mock(return_value="")
    monkeypatch.setattr("geo_blog.site_preview.command", run)
    report = {
        "environment": "local production build",
        "results": [
            {
                "route": "/blog/sample",
                "passed": True,
                "scores": {
                    "performance": 98,
                    "accessibility": 100,
                    "best-practices": 100,
                    "seo": 100,
                },
            }
        ],
    }
    path = web / "lighthouse-audit/summary.json"
    path.write_text(json.dumps(report))
    assert check_production_quality(tmp_path, "sample")["scores"]["seo"] == 100
    assert run.call_args_list[0].args[0][:2] == ["env", "VERCEL_ENV=production"]
    for bad in [69, 99, None]:
        report["results"][0]["scores"]["seo"] = bad
        path.write_text(json.dumps(report))
        with pytest.raises(ValueError):
            check_production_quality(tmp_path, "sample")
    with pytest.raises(ValueError):
        check_production_quality(tmp_path, "different-article")


def test_review_labels_production_build_scores_without_claiming_live_audit():
    draft = {
        "front_matter": {"title": "Title", "description": "Summary"},
        "report": {"word_count": 1000},
        "production_lighthouse": {
            "environment": "local production build",
            "scores": {"performance": 98, "accessibility": 100, "best-practices": 100, "seo": 100},
        },
    }
    card = json.dumps(review_blocks(draft, "draft-id"))
    assert "Production-build Lighthouse (local, median of 3)" in card
    assert "SEO 100" in card
    assert "Live scores are checked after publication" in card


def test_preview_preflight_requires_foundation_on_main(tmp_path, monkeypatch):
    from geo_blog.settings import Settings
    from geo_blog.site_preview import REPOSITORY, preflight

    settings = Settings(_env_file=None, storage_dir=tmp_path, website_repository=REPOSITORY)
    run = Mock(return_value="geo-web/package.json")
    monkeypatch.setattr("geo_blog.site_preview.command", run)
    with pytest.raises(ValueError, match="foundation must be merged"):
        preflight(settings)
    run.return_value = "\n".join(
        [
            "geo-web/scripts/check-lighthouse.mjs",
            "geo-web/scripts/prepare-blog-images.mjs",
            "geo-web/src/components/ui/BlogPhoto.tsx",
            ".github/workflows/blog-quality.yml",
        ]
    )
    preflight(settings)
    assert "/git/trees/main?" in run.call_args.args[0][2]


def test_ci_quality_uses_latest_exact_commit_result(monkeypatch):
    from geo_blog.site_preview import require_ci_quality

    check = {
        "id": 1,
        "name": "lighthouse",
        "app": {"slug": "github-actions"},
        "head_sha": "exact",
        "status": "completed",
        "conclusion": "success",
    }
    run = Mock(return_value=json.dumps({"check_runs": [check]}))
    monkeypatch.setattr("geo_blog.site_preview.command", run)
    monkeypatch.setattr("geo_blog.site_preview.time.sleep", lambda _: None)
    require_ci_quality(Path("."), "exact")
    for changed in [dict(check, head_sha="other"), dict(check, app={"slug": "other"})]:
        run.return_value = json.dumps({"check_runs": [changed]})
        with pytest.raises(ValueError, match="pending or missing"):
            require_ci_quality(Path("."), "exact")
    run.return_value = json.dumps({"check_runs": [check, dict(check, id=2, conclusion="failure")]})
    with pytest.raises(ValueError, match="check failed"):
        require_ci_quality(Path("."), "exact")


def test_preview_commands_supply_cli_paths_for_launchd(tmp_path, monkeypatch):
    from geo_blog.site_preview import command

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(Path, "exists", lambda _: True)
    run = Mock(return_value=Mock(returncode=0, stdout="ready", stderr=""))
    monkeypatch.setattr("geo_blog.site_preview.subprocess.run", run)
    assert command(["gh", "--version"], tmp_path) == "ready"
    path = run.call_args.kwargs["env"]["PATH"]
    assert "/opt/homebrew/bin" in path and "v22.19.0/bin" in path
    assert path.endswith("/usr/bin:/bin")


def test_preview_preflight_can_read_pinned_test_foundation(tmp_path, monkeypatch):
    """A stacked test preview must inspect its pinned source rather than production."""
    from geo_blog.settings import Settings
    from geo_blog.site_preview import REPOSITORY, preflight

    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        website_repository=REPOSITORY,
        website_base_branch="fix/blog-agent-foundation",
        website_base_commit="a" * 40,
    )
    run = Mock(
        return_value="\n".join(
            [
                "geo-web/scripts/check-lighthouse.mjs",
                "geo-web/scripts/prepare-blog-images.mjs",
                "geo-web/src/components/ui/BlogPhoto.tsx",
                ".github/workflows/blog-quality.yml",
            ]
        )
    )
    monkeypatch.setattr("geo_blog.site_preview.command", run)
    preflight(settings)
    assert "/git/trees/" + "a" * 40 + "?" in run.call_args.args[0][2]


def test_basecamp_review_shows_supporting_terms_without_inventing_secondary():
    from geo_blog.slack_app import keyword_summary

    text = keyword_summary(
        {
            "keyword": "old insulation",
            "secondary_keyword": "",
            "supporting_keywords": ["repair or replace", "insulation repair"],
        }
    )
    assert "Main keyword: old insulation" in text
    assert "Supporting keywords: repair or replace, insulation repair" in text
    assert "Secondary keyword:" not in text


def test_prose_metadata_uses_the_website_export_limits():
    """An article must not pass prose review with metadata its exporter rejects."""
    from geo_blog.content import validate

    def checked(description: str) -> dict:
        article = f"---\ntitle: Sample\nslug: sample\ndescription: {description}\nkeywords: [sample]\n---\n# Sample\n\nText."
        return {c["rule_key"]: c for c in validate(article, "sample", set())[2].summary["checks"]}

    assert not checked("x" * 125)["description"]["passed"]
    assert checked("x" * 150)["description"]["passed"]
    assert not checked("x" * 156)["description"]["passed"]


def test_bold_markdown_link_remains_clickable():
    """Bold CTA wrappers must not expose raw Markdown in the website."""
    assert inline("**[Get a Free Estimate](https://geo-insulation.com/contact)**") == [
        {"text": "Get a Free Estimate", "href": "https://geo-insulation.com/contact"}
    ]


def test_completed_parent_contains_preview_and_github_links():
    """The original announcement becomes a useful entry point to the finished draft."""
    from geo_blog.slack_app import completed_topic_message

    text = completed_topic_message(
        {
            "front_matter": {
                "title": "Insulation options",
                "description": "Repair or replacement guidance.",
            },
            "topic": {
                "keyword": "insulation repair",
                "supporting_keywords": ["insulation replacement"],
            },
            "report": {"word_count": 2200},
            "preview_url": "https://preview.vercel.app/blog/insulation",
            "pr_url": "https://github.com/example/site/pull/1",
        }
    )
    assert (
        "Open Vercel blog preview" in text and "https://preview.vercel.app/blog/insulation" in text
    )
    assert "Open GitHub draft" in text and "https://github.com/example/site/pull/1" in text
    assert "Supporting keywords: insulation replacement" in text
