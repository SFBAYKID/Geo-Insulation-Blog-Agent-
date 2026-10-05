import json
from unittest.mock import Mock

import pytest
from anthropic.types import Message

from geo_blog.claude_usage import usage_receipt
from geo_blog.content import Writer
from geo_blog.edits import apply_edits
from geo_blog.evidence import catalog_packet, selected_packet, source_context
from geo_blog.settings import Settings


def message(text="Evidence", usage=None):
    return Message(
        id="msg-test",
        type="message",
        role="assistant",
        model="claude-sonnet-4-6",
        content=[{"type": "text", "text": text}],
        stop_reason="end_turn",
        usage=usage or {"input_tokens": 1, "output_tokens": 1},
    )


def test_packets_preserve_selected_product_full_text_and_bound_other_products():
    selected = {
        "url": "https://geo-insulation.com/agents/selected/",
        "text": "Verified fact. " * 900 + " Requires human approval.",
    }
    other = {
        "url": "https://geo-insulation.com/agents/other/",
        "text": "Different product. " * 900,
    }
    company = [
        selected,
        other,
        {
            "url": "https://geo-insulation.com/blog/related/",
            "text": "Related post. " * 500,
        },
    ]
    topic = {"keyword": "Example", "product": {"url": selected["url"]}}
    packet = selected_packet(company, topic)
    assert packet[0] == selected
    assert other["url"] not in {p["url"] for p in packet}
    assert len(packet[1]["text"]) < 1000
    assert all(len(p["text"]) < 1900 for p in catalog_packet(company))
    assert all(
        p["text"].endswith("[Excerpt ends; omitted content is not evidence.]")
        for p in catalog_packet(company)
    )


def test_editor_prefix_is_identical_before_and_after_visual_metadata():
    topic = {
        "keyword": "Example",
        "product": {"url": "https://geo-insulation.com/agents/example/"},
    }
    company = [
        {
            "url": topic["product"]["url"],
            "text": "Verified capability; approval required.",
        }
    ]
    a = source_context(topic, company, "Research", {"https://example.test/", "https://other.test/"})
    b = source_context(
        dict(
            topic,
            hero={"src": "hero.webp"},
            exercise={"section": "Try"},
            claude_cost_estimate={"cost": 1},
        ),
        company,
        "Research",
        {"https://other.test/", "https://example.test/"},
    )
    assert a == b and "approval required" in b


def test_company_parser_omits_navigation_but_keeps_discovery_links_and_article_header():
    from geo_blog.company import TextParser

    p = TextParser()
    p.feed(
        '<nav><a href="/agents/one/">Menu</a></nav><main><h1>Product</h1>Human approval is required.</main><footer>Cookies</footer>'
    )
    assert p.links == ["/agents/one/"]
    assert "Menu" not in p.parts and "Cookies" not in p.parts
    assert "Human approval is required." in p.parts


def test_exact_edits_keep_unrelated_bytes_and_do_not_chain_replacements():
    original = "Title: Old\n\nBody “unchanged”.\nEnd"
    assert (
        apply_edits(original, [{"old": "Title: Old", "new": "Title: New"}])
        == "Title: New\n\nBody “unchanged”.\nEnd"
    )
    assert (
        apply_edits("abc def", [{"old": "abc", "new": "def"}, {"old": "def", "new": "ghi"}])
        == "def ghi"
    )


@pytest.mark.parametrize(
    "original,edits",
    [
        ("repeat repeat", [{"old": "repeat", "new": "x"}]),
        ("abcdef", [{"old": "abc", "new": "x"}, {"old": "bc", "new": "y"}]),
        ("abc", [{"old": "", "new": "x"}]),
        ("abc", [{"old": "missing", "new": "x"}]),
        ("abc", [{"old": "abc", "new": None}]),
    ],
)
def test_ambiguous_or_malformed_edits_fail_closed(original, edits):
    with pytest.raises(ValueError):
        apply_edits(original, edits)


def test_call_allowance_survives_restart_and_counts_timeout(tmp_path):
    settings = Settings(_env_file=None, storage_dir=tmp_path)
    client = Mock()
    client.messages.stream.side_effect = TimeoutError("unknown provider outcome")
    writer = Writer(settings, client)
    writer.bind_run(tmp_path / "run", max_calls=1)
    with pytest.raises(TimeoutError):
        writer.call("System", "Test")
    restarted = Writer(settings, client)
    restarted.bind_run(tmp_path / "run", max_calls=1)
    with pytest.raises(RuntimeError, match="allowance exhausted"):
        restarted.call("System", "Test")
    client.messages.stream.assert_called_once()


def test_unclassified_search_cache_writes_are_not_omitted_from_cost():
    response = message(
        usage={
            "input_tokens": 10,
            "output_tokens": 10,
            "cache_creation_input_tokens": 5000,
            "cache_creation": {
                "ephemeral_1h_input_tokens": 1000,
                "ephemeral_5m_input_tokens": 1000,
            },
        }
    )
    receipt = usage_receipt(response, {"model": "claude-sonnet-4-6"})
    assert receipt["unclassified_cache_creation_input_tokens"] == 3000
    assert receipt["cost_is_upper_estimate"]
    assert receipt["estimated_cost_usd"] == pytest.approx(
        (10 * 3 + 4000 * 6 + 1000 * 3.75 + 10 * 15) / 1e6
    )


def test_retry_uses_saved_company_product_and_research_without_paid_calls(tmp_path, monkeypatch):
    import geo_blog.company as company_module
    import geo_blog.product_fit as product_module

    company = [
        {
            "url": "https://geo-insulation.com/agents/example/",
            "text": "Verified human-reviewed product.",
        }
    ]
    topic = {"id": "one", "keyword": "example"}
    product = {"name": "Example", "url": company[0]["url"], "reason": "Fits"}
    (tmp_path / "company.json").write_text(json.dumps(company))
    (tmp_path / "product-plan.json").write_text(json.dumps(product))
    research = message()
    raw = research.model_dump(mode="json")
    raw["content"][0]["citations"] = [
        {
            "type": "web_search_result_location",
            "url": "https://www.energy.gov/test",
            "title": "Docs",
            "encrypted_index": "test",
            "cited_text": "Evidence",
        }
    ]
    (tmp_path / "research.json").write_text(json.dumps(raw))
    monkeypatch.setattr(
        company_module,
        "company_evidence",
        Mock(side_effect=AssertionError("Do not fetch again")),
    )
    monkeypatch.setattr(
        product_module,
        "plan_product",
        Mock(side_effect=AssertionError("Do not select again")),
    )
    writer = Writer(Settings(_env_file=None), Mock())
    writer.call = Mock(side_effect=AssertionError("Do not research again"))
    writer.write_from_research = Mock(return_value={"saved": True})
    assert writer.draft(topic, "2026-09-18", tmp_path) == {"saved": True}
    assert writer.write_from_research.call_args.args[4] == "Evidence"
    writer.call.assert_not_called()
    with pytest.raises(ValueError, match="different brief"):
        writer.draft(dict(topic, keyword="different"), "2026-09-18", tmp_path)


def test_revision_sends_brief_not_provider_transcript_and_reconstructs_patch(tmp_path, monkeypatch):
    import geo_blog.revisions as revisions

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    folder = tmp_path / "revision"
    folder.mkdir()
    (evidence / "company.json").write_text("[]")
    raw = message("Concise verified brief").model_dump(mode="json")
    raw["private_tool_transcript"] = "HUGE PROVIDER TRANSCRIPT MUST NOT BE SENT"
    (evidence / "research.json").write_text(json.dumps(raw))
    base = {
        "markdown": "---\ntitle: Old\nslug: example\ndescription: Example\nkeywords: [Example]\n---\n# Heading\n\nBody unchanged.",
        "front_matter": {"title": "Old"},
        "topic": {"keyword": "Example"},
        "sources": [],
        "preview_url": "old",
        "qa_summary": "old",
    }
    plan = {
        "edits": [{"old": "title: Old", "new": "title: New"}],
        "summary": "Title",
        "hero": None,
        "exercise": None,
    }
    writer = Mock()
    writer.call.side_effect = [
        message(json.dumps(plan)),
        message('{"verdict":"approve","notes":[]}'),
    ]
    monkeypatch.setattr(
        revisions,
        "validate_plan",
        lambda b, p, r: dict(b, markdown=p["markdown"], front_matter={"title": "New"}),
    )
    result = revisions.prepare(
        Settings(_env_file=None),
        {"base_payload": json.dumps(base), "request": "Change title"},
        folder,
        evidence,
        writer,
    )
    assert result["markdown"] == base["markdown"].replace("title: Old", "title: New")
    for call in writer.call.call_args_list:
        packet = "".join(call.kwargs["cache_parts"])
        assert "Concise verified brief" in packet
        assert "HUGE PROVIDER TRANSCRIPT" not in packet and "qa_summary" not in packet
    assert writer.call.call_args_list[0].kwargs["max_tokens"] == 4000
    assert "preview_url" not in result


def test_oversized_prompt_is_rejected_before_network_or_allowance_use(tmp_path):
    client = Mock()
    writer = Writer(Settings(_env_file=None), client)
    writer.bind_run(tmp_path)
    with pytest.raises(ValueError, match="context limit"):
        writer.call("System", "x" * 120001)
    client.messages.stream.assert_not_called()
    assert not (tmp_path / "claude-call-budget.json").exists()
