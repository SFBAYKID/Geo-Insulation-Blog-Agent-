"""Article validation and delivery regressions with synthetic inputs."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from geo_blog.content import WRITER_ATTEMPTS, validate
from geo_blog.settings import Settings


def test_a_capped_research_brief_is_retried_with_more_room(tmp_path, monkeypatch, valid_article):
    """September 19, 2026: research ran out of room and the whole night ended with no draft."""
    from geo_blog.content import Writer

    monkeypatch.setattr("geo_blog.content.primary_urls", lambda urls: urls)
    monkeypatch.setattr(
        "geo_blog.company.company_evidence",
        lambda topic=None: [
            {
                "url": "https://geo-insulation.com/",
                "text": "Geo Insulation",
            }
        ],
    )
    monkeypatch.setattr(
        "geo_blog.product_fit.plan_product",
        lambda *args: {
            "name": "Example",
            "url": "https://geo-insulation.com/",
            "reason": "Test fixture",
        },
    )
    evidence = {
        "content": [
            {"type": "web_search_result", "url": u}
            for u in [
                "https://geo-insulation.com/",
                "https://example.com/docs",
                "https://example.org/research",
            ]
        ]
    }

    def message(text, stop="end_turn", raw=None):
        return SimpleNamespace(
            stop_reason=stop,
            content=[SimpleNamespace(type="text", text=text)],
            model_dump=lambda **kwargs: raw or {},
            model_dump_json=lambda **kwargs: "{}",
        )

    writer = Writer(
        Settings(_env_file=None, writer_patch_corrections=True, storage_dir=tmp_path), client=Mock()
    )
    writer.call = Mock(
        side_effect=[
            message("brief cut off", "max_tokens", evidence),
            message("Research", raw=evidence),
            message(valid_article),
            message('{"verdict":"approve","notes":[]}'),
        ]
    )
    draft = writer.draft({"keyword": "AI workflow automation"}, "2026-09-21", tmp_path)
    allowances = [call.kwargs["max_tokens"] for call in writer.call.call_args_list[:2]]
    assert allowances[1] > allowances[0], "the retry needs more room than the attempt that ran out"
    assert draft["markdown"] == valid_article


def test_an_unsourced_link_is_named_so_it_can_be_fixed(valid_article):
    """ "fail" alone gave the writer nothing to act on and burned every retry."""

    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    article = valid_article.replace(
        "https://example.com/docs", "https://support.google.com/mail/answer/81126"
    )
    failed = {
        c.rule_key: c.actual
        for c in validate(article, "AI workflow automation", urls)[2].hard_failures
    }
    assert "https://support.google.com/mail/answer/81126" in failed["known_links"]
    assert "https://geo-insulation.com/" not in failed["known_links"], (
        "only the unsourced link is named"
    )


def test_correction_feedback_shows_passing_rules_so_they_are_not_broken(
    tmp_path, monkeypatch, valid_article
):
    """September 21, 2026: each attempt fixed one description rule and broke the other."""
    from geo_blog.content import Writer, parse_draft

    monkeypatch.setattr("geo_blog.content.primary_urls", lambda urls: urls)
    fm, body = parse_draft(valid_article)
    short = "---\n" + yaml.safe_dump(dict(fm, description="Too short.")) + "---\n" + body

    def message(text):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            model_dump_json=lambda **kwargs: "{}",
        )

    writer = Writer(
        Settings(_env_file=None, writer_patch_corrections=True, storage_dir=tmp_path), client=Mock()
    )
    writer.call = Mock(
        side_effect=[message(short)] + [message('{"edits":[]}')] * (WRITER_ATTEMPTS - 1)
    )
    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    with pytest.raises(ValueError, match="failed quality review"):
        writer.write_from_research(
            {"keyword": "AI workflow automation"},
            "2026-09-21",
            tmp_path,
            [],
            "Evidence",
            urls,
        )
    sent = writer.call.call_args_list[1].args[1]
    assert '"rule_key": "description"' in sent and '"passed": false' in sent
    assert '"rule_key": "citations"' in sent, "rules already satisfied must be visible too"
    assert "without breaking any check marked passed" in sent


def test_brief_urls_outside_the_link_check_are_named_as_unusable():
    """September 22, 2026: the brief told the writer to cite an FTC URL its search never
    returned, so the link check and the editor rejected each other's fix for five attempts."""
    from geo_blog.content import PRIMARY_DOMAINS, unusable_urls

    brief = (
        "Legal anchor: `https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business`.\n"
        "| Salesforce | https://www.salesforce.com/blog/x/ | timing |"
    )
    assert unusable_urls(brief, {"https://www.salesforce.com/blog/x/"}) == [
        "https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business"
    ]
    assert "ftc.gov" in PRIMARY_DOMAINS
