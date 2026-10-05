"""Article validation and delivery regressions with synthetic inputs."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import yaml

from geo_blog.content import WRITER_ATTEMPTS, validate
from geo_blog.settings import Settings
from geo_blog.store import Store
from geo_blog.topics import select_topic


def test_export_flat_planned_url_keeps_content_in_hub_and_uses_existing_component(
    tmp_path, valid_article
):
    from geo_blog.website import article_location, export_preview, flat_route

    store = Store(tmp_path / "state")
    payload = {
        "markdown": valid_article,
        "topic": {
            "hub": "installation-process",
            "planned_url": "/blog/handoff-email-template/",
        },
    }
    store.reserve("flat", "flat", "topic")
    store.save("flat", payload)
    site = tmp_path / "site"
    assert export_preview(store, "flat", site) == "/blog/handoff-email-template/"
    location = article_location(payload)
    assert (
        location["mdx_path"] == "src/content/blog/installation-process/handoff-email-template.mdx"
    )
    assert "path: /blog/handoff-email-template/" in (site / location["mdx_path"]).read_text()
    assert (site / location["route_path"]).read_text() == flat_route(
        "installation-process", "handoff-email-template"
    )
    assert "if (!post) notFound()" in (site / location["route_path"]).read_text()
    share = (site / "src/app/blog/handoff-email-template/opengraph-image.tsx").read_text()
    assert "<BlogPostImage title={post.title} />" in share and "if (!post) notFound()" in share


@pytest.mark.parametrize(
    "planned",
    [
        "/blog/installation-process/",
        "/blog/media/",
        "https://evil.test/blog/slug/",
        "/blog/slug/?x=1",
        "/blog/../slug/",
    ],
)
def test_flat_url_cannot_shadow_hubs_or_escape_site(valid_article, planned):
    from geo_blog.website import article_location

    with pytest.raises(ValueError):
        article_location({"markdown": valid_article, "topic": {"planned_url": planned}})


def test_export_flat_url_never_overwrites_existing_route(tmp_path, valid_article):
    from geo_blog.website import export_preview

    st = Store(tmp_path / "state")
    st.reserve("flat", "flat", "topic")
    st.save("flat", {"markdown": valid_article, "topic": {"planned_url": "/blog/occupied/"}})
    route = tmp_path / "site/src/app/blog/occupied/page.tsx"
    route.parent.mkdir(parents=True)
    route.write_text("existing page")
    with pytest.raises(ValueError, match="already has"):
        export_preview(st, "flat", tmp_path / "site")
    assert route.read_text() == "existing page"


def test_real_queue_skips_done_folded_used_and_selects_primary_secondary():
    records = [
        {
            "id": key,
            "fields": {
                "Status": status,
                "Primary keyword": key,
                "Supporting keywords": "support phrase; another phrase",
                "Target size (words)": "1,800–2,400 words — provisional editorial scope",
            },
        }
        for key, status in [
            ("published", "Published"),
            ("folded", "Fold into existing"),
            ("used", "Proposed"),
            ("fresh", "Proposed"),
        ]
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"records": records}))
    )
    s = Settings(
        _env_file=None,
        writer_patch_corrections=True,
        airtable_token="test",
        airtable_base_id="base",
    )
    t = select_topic(s, {"used"}, client)
    assert t["id"] == "fresh" and t["secondary_keyword"] == "support phrase"
    assert t["target_words"] == 2100


def test_incomplete_queue_is_not_reported_as_exhausted():
    from geo_blog.topics import IncompleteKeywordQueue

    s = Settings(
        _env_file=None,
        writer_patch_corrections=True,
        airtable_token="test",
        airtable_base_id="base",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "id": "missing",
                            "fields": {"Status": "Proposed", "Primary keyword": "AI"},
                        }
                    ]
                },
            )
        )
    )
    with pytest.raises(IncompleteKeywordQueue):
        select_topic(s, set(), client)


def test_exhausted_queue_notifies_slack_once_without_generating(tmp_path, monkeypatch):
    from geo_blog.cli import run_daily
    from geo_blog.slack_app import queue_notice

    s = Settings(
        _env_file=None,
        openai_api_key="test",
        slack_bot_token="test",
        slack_approver_ids="owner",
    )
    st = Store(tmp_path)
    monkeypatch.setattr("geo_blog.cli.select_topic", lambda *args: None)
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    client.chat_postMessage.return_value = {"ts": "123.456"}
    monkeypatch.setattr(
        "geo_blog.slack_app.queue_notice",
        lambda settings, store, day, kind, msg: queue_notice(
            settings, store, day, kind, msg, client
        ),
    )
    for _ in range(2):
        assert run_daily(s, st, send=True) is None
    assert client.chat_postMessage.call_count == 1
    assert "completely out of keywords" in client.chat_postMessage.call_args.kwargs["text"]
    assert st.used_topics() == set()


def test_duplicate_primary_owned_by_published_row_is_not_redrafted():
    records = [
        {
            "id": "fresh",
            "fields": {
                "Status": "Proposed",
                "Primary keyword": "Same term",
                "Supporting keywords": "support",
            },
        },
        {
            "id": "done",
            "fields": {"Status": "Published", "Primary keyword": "same TERM"},
        },
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"records": records}))
    )
    s = Settings(
        _env_file=None,
        writer_patch_corrections=True,
        airtable_token="test",
        airtable_base_id="base",
    )
    assert select_topic(s, set(), client) is None


def test_queue_connection_error_never_claims_keywords_exhausted(tmp_path, monkeypatch):
    from geo_blog.cli import run_daily

    settings = Settings(
        _env_file=None,
        openai_api_key="test",
        slack_bot_token="test",
        slack_approver_ids="owner",
    )

    def unavailable(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("geo_blog.cli.select_topic", unavailable)
    notice = Mock()
    monkeypatch.setattr("geo_blog.slack_app.queue_notice", notice)
    with pytest.raises(httpx.ConnectError):
        run_daily(settings, Store(tmp_path), send=True)
    assert "queue-error" == notice.call_args.args[3]
    assert "completely out of keywords" not in notice.call_args.args[4]


def test_approval_card_does_not_repeat_uploaded_file():
    from geo_blog.slack_app import review_blocks

    draft = {
        "front_matter": {"title": "Review", "description": "Article"},
        "report": {"word_count": 1000},
        "preview_url": "https://preview.vercel.app/blog/",
    }
    blocks = review_blocks(draft, "one")
    encoded = json.dumps(blocks)
    assert "Open website preview" in encoded and "Read the full draft" not in encoded
    assert len([b for b in blocks if b["type"] == "actions"]) == 1
    del draft["preview_url"]
    assert "preview is not available" in json.dumps(review_blocks(draft, "one"))


def test_title_limit_matches_the_website_required_check(valid_article):
    """A 66-character title passed here and production rejected it on September 18, 2026."""
    from geo_blog.content import parse_draft, validate

    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    fm, body = parse_draft(valid_article)

    def rebuild(title):
        return "---\n" + yaml.safe_dump(dict(fm, title=title)) + "---\n" + body

    too_long = "AI workflow automation: choose the first job to hand over today"
    assert 60 < len(too_long) <= 70, "the old limit accepted this length"
    report = validate(rebuild(too_long), "AI workflow automation", urls)[2]
    assert "title" in {c.rule_key for c in report.hard_failures}
    assert validate(
        rebuild("AI workflow automation: choose the first job"),
        "AI workflow automation",
        urls,
    )[2].passed


def test_approved_prose_checkpoint_skips_generation_but_checks_integrity(tmp_path, valid_article):
    from geo_blog.content import Writer

    def message(text):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            model_dump_json=lambda **kwargs: "{}",
        )

    writer = Writer(Settings(_env_file=None, writer_patch_corrections=True), client=Mock())
    writer.call = Mock(
        side_effect=[
            message(valid_article),
            message('{"verdict":"approve","notes":[]}'),
        ]
    )
    args = (
        {"keyword": "AI workflow automation"},
        "2026-09-18",
        tmp_path,
        [],
        "Evidence",
        {
            "https://geo-insulation.com/",
            "https://example.com/docs",
            "https://example.org/research",
        },
    )
    first = writer.write_from_research(*args)
    writer.call.reset_mock(side_effect=True)
    writer.call.side_effect = AssertionError("Approved article must not regenerate")
    assert writer.write_from_research(*args) == first
    writer.call.assert_not_called()
    checkpoint = tmp_path / "approved-prose.json"
    saved = json.loads(checkpoint.read_text())
    saved["draft"]["markdown"] = "Corrupt changed content"
    checkpoint.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="checksum mismatch"):
        writer.write_from_research(*args)
    writer.call.assert_not_called()


def research_message(stop_reason, text):
    from geo_blog.model_response import Message

    return Message.model_validate(
        {
            "id": "msg_research",
            "type": "message",
            "role": "assistant",
            "model": "gpt-6-luna",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }
    )


def test_truncated_research_is_never_saved_or_reused(tmp_path):
    """September 19, 2026: a truncated brief was saved before it was checked, so every
    later retry reloaded the same incomplete response and failed at the same line."""
    from geo_blog.content import Writer

    product = "https://geo-insulation.com/agents/finance-collections-agent/"
    (tmp_path / "company.json").write_text(
        json.dumps([{"url": product, "text": "Finance collections agent page."}])
    )
    (tmp_path / "product-plan.json").write_text(json.dumps({"url": product}))
    topic = {"keyword": "refund processed email template"}
    writer = Writer(Settings(_env_file=None, writer_patch_corrections=True), client=Mock())
    writer.call = Mock(return_value=research_message("max_tokens", "Partial brief"))
    with pytest.raises(ValueError, match="Incomplete model response"):
        writer.draft(topic, "2026-09-21", tmp_path)
    assert not (tmp_path / "research.json").exists()
    (tmp_path / "research.json").write_text(
        research_message("max_tokens", "Partial brief").model_dump_json()
    )
    writer.call = Mock(return_value=research_message("end_turn", "Complete brief"))
    with pytest.raises(ValueError, match="no verifiable search results"):
        writer.draft(topic, "2026-09-21", tmp_path)
    assert writer.call.call_count == 1
    assert json.loads((tmp_path / "research.json").read_text())["stop_reason"] == "end_turn"


def test_repair_edits_are_schema_enforced_so_quoted_prose_parses(tmp_path, valid_article):
    """September 21, 2026: edits quoting the article returned unescaped quotes, failed to
    parse, and used up the last writing attempt."""
    from geo_blog.content import EDITS_FORMAT, Writer

    def message(text):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            model_dump_json=lambda **kwargs: "{}",
        )

    quoted = json.dumps(
        {
            "edits": [
                {
                    "old": "### Choose",
                    "new": '## Choose\n\nThey read "your refund has been processed" carefully.',
                }
            ]
        }
    )
    writer = Writer(Settings(_env_file=None, writer_patch_corrections=True), client=Mock())
    writer.call = Mock(
        side_effect=[
            message(valid_article.replace("## Choose", "### Choose")),
            message(quoted),
            message('{"verdict":"approve","notes":[]}'),
        ]
    )
    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    draft = writer.write_from_research(
        {"keyword": "AI workflow automation"},
        "2026-09-21",
        tmp_path,
        [],
        "Evidence",
        urls,
    )
    assert "your refund has been processed" in draft["markdown"]
    repair = writer.call.call_args_list[1]
    assert repair.kwargs["output_config"] == EDITS_FORMAT
    assert "output_config" not in writer.call.call_args_list[0].kwargs


def test_keyword_in_the_h1_alone_does_not_satisfy_the_intro(valid_article):
    """September 21, 2026: the exporter strips the body H1, so an article whose only
    early mention sat in that heading published without the phrase anywhere in its prose."""
    h1_only = valid_article.replace("AI workflow automation starts with one clear handoff. ", "")
    report = validate(
        h1_only,
        "AI workflow automation",
        {
            "https://geo-insulation.com/",
            "https://example.com/docs",
            "https://example.org/research",
        },
    )[2]
    assert "keyword_intro" in {c.rule_key for c in report.hard_failures}
    assert validate(
        valid_article,
        "AI workflow automation",
        {
            "https://geo-insulation.com/",
            "https://example.com/docs",
            "https://example.org/research",
        },
    )[2].passed


def test_a_pointless_edit_never_discards_the_real_corrections():
    """September 21, 2026: two no-op edits overlapped a real one and all five were thrown out."""
    from geo_blog.edits import apply_edits

    article = 'description: "templates for old clients"\n\nSee ([Google Support](https://example.com/a)).\n'
    edits = [
        {
            "old": 'description: "templates for old clients"',
            "new": 'description: "a template for old clients"',
        },
        {
            "old": "[Google Support](https://example.com/a))",
            "new": "[Google Support](https://example.com/a))",
        },
        {
            "old": "([Google Support](https://example.com/a))",
            "new": "([Google Docs](https://example.com/b))",
        },
    ]
    result = apply_edits(article, edits)
    assert "a template for old clients" in result
    assert "https://example.com/b" in result and "https://example.com/a" not in result


def test_edits_that_change_nothing_are_sent_back_rather_than_applied():
    from geo_blog.edits import apply_edits

    article = 'title: "Keep me"\n'
    with pytest.raises(ValueError, match="No edit changed the article"):
        apply_edits(article, [{"old": 'title: "Keep me"', "new": 'title: "Keep me"'}])
    with pytest.raises(ValueError, match="No edit changed the article"):
        apply_edits(article, [])


def test_a_rejected_patch_keeps_the_findings_it_was_meant_to_fix(
    tmp_path, monkeypatch, valid_article
):
    """The mechanical error used to replace the findings, so the next attempt flew blind."""
    from geo_blog.content import Writer

    monkeypatch.setattr("geo_blog.content.primary_urls", lambda urls: urls)

    def message(text):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            model_dump_json=lambda **kwargs: "{}",
        )

    writer = Writer(
        Settings(_env_file=None, writer_patch_corrections=True, storage_dir=tmp_path), client=Mock()
    )
    # A draft missing its secondary keyword, then a patch whose old text is not in it.
    writer.call = Mock(
        side_effect=[message(valid_article)]
        + [message('{"edits":[{"old":"absent text","new":"x"}]}')] * (WRITER_ATTEMPTS - 1)
    )
    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    with pytest.raises(ValueError, match="failed quality review"):
        writer.write_from_research(
            {
                "keyword": "AI workflow automation",
                "secondary_keyword": "human in the loop AI",
            },
            "2026-09-21",
            tmp_path,
            [],
            "Evidence",
            urls,
        )
    sent = writer.call.call_args_list[2].args[1]
    assert "secondary_keyword" in sent, "the structural findings must survive a rejected patch"
    assert "could not be applied" in sent, "and the reply problem must be named too"
