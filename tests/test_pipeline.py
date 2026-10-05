"""Article validation and delivery regressions with synthetic inputs."""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from geo_blog.content import WRITER_ATTEMPTS, researched_urls, response_text, validate
from geo_blog.settings import Settings
from geo_blog.slack_app import deliver
from geo_blog.store import Store
from geo_blog.topics import select_topic


def test_quality_rejects_invented_urls_and_thin_output(valid_article):
    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    assert validate(valid_article, "AI workflow automation", urls)[2].passed
    report = validate(valid_article, "AI workflow automation", set())[2]
    assert "known_links" in {c.rule_key for c in report.hard_failures}
    report = validate(
        valid_article.replace(
            "A clear task has inputs, actions, exceptions and a review step. " * 75,
            "Short.",
        ),
        "AI workflow automation",
        urls,
    )[2]
    assert "depth" in {c.rule_key for c in report.hard_failures}


def test_truncation_and_untrusted_links_fail():
    with pytest.raises(ValueError):
        response_text(SimpleNamespace(stop_reason="max_tokens", content=[]))
    assert (
        researched_urls({"type": "text", "text": "invented", "url": "https://fake.test"}) == set()
    )
    assert researched_urls(
        {"content": [{"type": "web_search_result", "url": "https://source.test"}]}
    ) == {"https://source.test"}


def test_durable_approval_and_duplicate_delivery(tmp_path):
    store = Store(tmp_path)
    assert store.reserve("one", "day:1", "topic")
    assert not store.reserve("two", "day:1", "another")
    store.save("one", {})
    assert store.claim_delivery("one", "channel")
    assert not store.claim_delivery("one", "channel")
    store.delivered("one", "123")
    args = dict(
        decision="approved",
        user="owner",
        channel="channel",
        message_ts="123",
        allowed_users={"owner"},
    )
    assert not store.decide("one", **dict(args, user="stranger"))
    assert not store.decide("one", **dict(args, message_ts="old"))
    assert not store.decide("one", **dict(args, channel="elsewhere"))
    assert store.decide("one", **args)
    assert not store.decide("one", **dict(args, decision="rejected"))
    assert Store(tmp_path).get("one")["status"] == "approved"


def test_slack_chunks_precede_buttons_and_timeout_stays_sending(tmp_path):
    s = Settings(
        _env_file=None,
        slack_bot_token="test",
        slack_approver_ids="owner",
        storage_dir=tmp_path,
    )
    store = Store(tmp_path)
    store.reserve("one", "day", "topic")
    store.save(
        "one",
        {
            "front_matter": {"title": "Test", "description": "Review"},
            "markdown": "Full draft",
            "report": {"word_count": 1000},
        },
    )
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    store.set_thread("one", s.slack_channel_id, "100.1")
    client.chat_postMessage.side_effect = TimeoutError
    with pytest.raises(TimeoutError):
        deliver(s, store, "one", client)
    assert client.chat_postMessage.call_count == 1
    assert store.get("one")["status"] == "sending"
    with pytest.raises(ValueError):
        deliver(s, store, "one", client)
    assert client.chat_postMessage.call_count == 1


def test_airtable_pagination_and_priority():
    def handler(request):
        if "offset" in request.url.params:
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "id": "new",
                            "fields": {
                                "Primary keyword": "AI",
                                "Supporting keywords": "AI workflows",
                                "Priority": 10,
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "id": "old",
                        "fields": {
                            "Primary keyword": "Old",
                            "Supporting keywords": "Old workflows",
                        },
                    }
                ],
                "offset": "page2",
            },
        )

    settings = Settings(
        _env_file=None,
        writer_patch_corrections=True,
        airtable_token="test",
        airtable_base_id="appTest",
    )
    topic = select_topic(settings, {"old"}, httpx.Client(transport=httpx.MockTransport(handler)))
    assert topic["id"] == "new"


def test_slack_success_is_reviewable(tmp_path):
    s = Settings(
        _env_file=None,
        writer_patch_corrections=True,
        slack_bot_token="test",
        slack_approver_ids="owner",
    )
    store = Store(tmp_path)
    store.reserve("one", "day", "topic")
    store.save(
        "one",
        {
            "front_matter": {"title": "Title", "description": "Description"},
            "markdown": "Full draft",
            "report": {"word_count": 1000},
        },
    )
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    client.files_upload_v2.return_value = {"files": [{"permalink": "https://slack.com/file"}]}
    client.chat_postMessage.return_value = {"ts": "123.1"}
    assert deliver(s, store, "one", client) == "123.1"
    assert store.get("one")["status"] == "pending"
    assert not any(c.kwargs["text"] == "Full draft" for c in client.chat_postMessage.call_args_list)
    assert client.chat_postMessage.call_args.kwargs["channel"] == s.slack_channel_id


def test_malformed_editor_never_approves(tmp_path, valid_article, monkeypatch):
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

    def message(text, raw=None):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            model_dump=lambda **kwargs: raw or {},
            model_dump_json=lambda **kwargs: "{}",
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
    monkeypatch.setattr(
        "geo_blog.product_fit.plan_product",
        lambda *args: {
            "name": "Example",
            "url": "https://geo-insulation.com/",
            "reason": "Test fixture",
        },
    )
    writer = Writer(Settings(_env_file=None, writer_patch_corrections=True), client=Mock())
    writer.call = Mock(
        side_effect=[message("Research", evidence)]
        + [
            item
            for i in range(WRITER_ATTEMPTS)
            for item in (
                message(valid_article if i == 0 else '{"edits":[]}'),
                message("not JSON"),
                message("not JSON"),
                message("not JSON"),
            )
        ]
    )
    with pytest.raises(ValueError, match="failed quality review"):
        writer.draft({"keyword": "AI workflow automation"}, "2026-09-14", tmp_path)
    assert not (tmp_path / "draft.md").exists()
    # Attempts two and three return edits that change nothing, so they are sent back for
    # corrected edits instead of paying an editor to review an unchanged draft.
    # Research, then the first draft with three editor replies, then one call per retry.
    assert writer.call.call_count == WRITER_ATTEMPTS + 4


def test_revision_writer_keeps_prior_prose_and_corrections_after_structure_failure(
    tmp_path, valid_article
):
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
            message(valid_article.replace("## Choose", "### Choose")),
            message('{"edits":[{"old":"### Choose","new":"## Choose"}]}'),
            message('{"verdict":"approve","notes":[]}'),
        ]
    )
    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    writer.write_from_research(
        {"keyword": "AI workflow automation"},
        "2026-09-17",
        tmp_path,
        [],
        "Evidence",
        urls,
        initial_text="Previously approved prose",
        initial_feedback="Preserve these verified facts",
    )
    assert "Previously approved prose" in writer.call.call_args_list[0].kwargs["cache_parts"][1]
    assert all(
        "Preserve these verified facts" in call.args[1] for call in writer.call.call_args_list[:2]
    )


def test_secondary_keyword_and_repetition_are_checked(valid_article):
    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    report = validate(valid_article, "AI workflow automation", urls, "human in the loop AI")[2]
    assert {c.rule_key for c in report.hard_failures} == {
        "secondary_keyword",
        "secondary_metadata",
        "secondary_h1",
        "secondary_title",
        "secondary_description",
    }
    report = validate(
        valid_article + " AI workflow automation" * 20, "AI workflow automation", urls
    )[2]
    assert "repetition" in {c.rule_key for c in report.hard_failures}


def test_preview_export_is_draft_with_single_template_heading(tmp_path, valid_article):
    from geo_blog.website import export_preview

    store = Store(tmp_path / "state")
    store.reserve("article", "practice:test", "example")
    store.save("article", {"markdown": valid_article, "topic": {"hub": "attic-insulation"}})
    checkout = tmp_path / "website"
    path = export_preview(store, "article", checkout)
    assert path == "/blog/attic-insulation/choose-first-job/"
    text = (checkout / "src/content/blog/attic-insulation/choose-first-job.mdx").read_text()
    assert "draft: true" in text
    assert "primaryKeyword: AI workflow automation" in text
    assert "\n# " not in text
    with pytest.raises(ValueError, match="overwrite"):
        export_preview(store, "article", checkout)
    store.attach_preview(
        "article", "https://geoinsulation-test.vercel.app" + path, "Measured report"
    )
    store.claim_delivery("article", "channel")
    with pytest.raises(ValueError):
        store.attach_preview("article", "https://geoinsulation-test.vercel.app" + path, "Changed")


def test_escaped_email_is_text_but_html_and_wrong_company_fail(valid_article):
    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    assert validate(valid_article + r"\<person@example.com\>", "AI workflow automation", urls)[
        2
    ].passed
    for addition, rule in [
        ("<script>alert(1)</script>", "plain_markdown"),
        ("Visit unrelated.example", "company_identity"),
    ]:
        report = validate(valid_article + addition, "AI workflow automation", urls)[2]
        assert rule in {c.rule_key for c in report.hard_failures}


def test_delivery_keeps_files_and_card_in_existing_thread(tmp_path):
    s = Settings(
        _env_file=None,
        writer_patch_corrections=True,
        slack_bot_token="test",
        slack_approver_ids="owner",
    )
    store = Store(tmp_path)
    store.reserve("one", "practice:thread", "topic")
    store.save(
        "one",
        {
            "front_matter": {"title": "Title", "description": "Description"},
            "markdown": "Article",
            "report": {"word_count": 1400},
        },
    )
    store.set_thread("one", s.slack_channel_id, "100.1")
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    client.files_upload_v2.return_value = {"files": [{"permalink": "https://slack.com/file"}]}
    client.chat_postMessage.return_value = {"ts": "101.1"}
    deliver(s, store, "one", client)
    assert client.chat_postMessage.call_count == 1
    assert client.chat_postMessage.call_args.kwargs["thread_ts"] == "100.1"
    assert client.chat_postMessage.call_args.kwargs["reply_broadcast"] is False
    assert all(c.kwargs["thread_ts"] == "100.1" for c in client.chat_postMessage.call_args_list)
    with pytest.raises(ValueError):
        store.set_thread("one", s.slack_channel_id, "200.1")


def test_saved_thread_cannot_be_sent_to_another_channel(tmp_path):
    s = Settings(
        _env_file=None,
        writer_patch_corrections=True,
        slack_bot_token="test",
        slack_approver_ids="owner",
    )
    store = Store(tmp_path)
    store.reserve("one", "practice:bound-thread", "topic")
    store.save("one", {})
    store.set_thread("one", "OTHER_CHANNEL", "100.1")
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    with pytest.raises(ValueError, match="another channel"):
        deliver(s, store, "one", client)
    client.files_upload_v2.assert_not_called()
    client.chat_postMessage.assert_not_called()
    assert store.get("one")["status"] == "ready"


def test_existing_editorial_fields_map_without_losing_constraints():
    from geo_blog.topics import topic_from_record

    t = topic_from_record(
        {
            "id": "record",
            "fields": {
                "Primary keyword": "AI workflow automation",
                "Working title": "Check the task",
                "Supporting keywords": "human in the loop AI (90/mo); more phrases",
                "Target size (words)": "1,400–1,800",
                "Hub": "/blog/installation-process/",
                "Planned URL": "/blog/installation-process/check-the-task/",
                "Must include": "Show an approval step",
                "Notes / rationale": "Preserve the source",
            },
        }
    )
    assert t["topic"] == "Check the task"
    assert t["secondary_keyword"] == "human in the loop AI"
    assert t["target_words"] == 1600 and t["hub"] == "installation-process"
    assert t["must_include"] == "Show an approval step"
    assert t["planned_url"] == "/blog/installation-process/check-the-task/"
    assert "(90/mo)" in t["supporting_keywords_brief"]


def test_queue_reads_all_rows_to_check_unwritten_status():
    def handler(request):
        assert request.url.params["pageSize"] == "100"
        assert "/appExisting/tblPosts" in request.url.path
        return httpx.Response(200, json={"records": []})

    s = Settings(
        _env_file=None,
        airtable_token="test",
        airtable_base_id="appExisting",
        airtable_table="tblPosts",
    )
    assert select_topic(s, set(), httpx.Client(transport=httpx.MockTransport(handler))) is None


def test_company_discovers_only_relevant_live_blog_pages(monkeypatch):
    from geo_blog import company

    fetched = []

    def handler(request):
        fetched.append(str(request.url))
        html = "<p>Verified page</p>"
        if request.url.path.rstrip("/") == "/blog":
            html += '<a href="/blog/installation-process/ai-workflow-guide/">Guide</a><a href="/blog/installation-process/gardening/">Unrelated</a><a href="https://outside.test/blog/installation-process/ai-workflow/">Wrong domain</a>'
        return httpx.Response(200, text=html)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(company.httpx, "Client", lambda **kwargs: client)
    pages = company.company_evidence({"keyword": "AI workflow automation"})
    assert "https://geo-insulation.com/blog/installation-process/ai-workflow-guide/" in {
        p["url"] for p in pages
    }
    assert not any("gardening" in u or "outside.test" in u for u in fetched)


def test_export_preserves_airtable_url_and_rejects_wrong_hub(tmp_path, valid_article):
    from geo_blog.website import export_preview

    store = Store(tmp_path / "state")
    store.reserve("planned", "practice:planned", "planned")
    store.save(
        "planned",
        {
            "markdown": valid_article,
            "topic": {
                "hub": "installation-process",
                "planned_url": "/blog/installation-process/assigned-url/",
            },
        },
    )
    assert (
        export_preview(store, "planned", tmp_path / "site")
        == "/blog/installation-process/assigned-url/"
    )
    store.reserve("wrong", "practice:wrong", "wrong")
    store.save(
        "wrong",
        {
            "markdown": valid_article,
            "topic": {
                "hub": "installation-process",
                "planned_url": "/blog/home-comfort/assigned-url/",
            },
        },
    )
    with pytest.raises(ValueError, match="assigned blog hub"):
        export_preview(store, "wrong", tmp_path / "site")


def test_unquoted_colon_front_matter_is_retryable_feedback():
    from geo_blog.content import parse_draft

    with pytest.raises(ValueError, match="double quotes"):
        parse_draft("---\ntitle: Air leaks: How It Works\nslug: x\n---\n# Body")
