"""Regression for the November 11, 2026 rerun: description stuck a few characters over."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from geo_blog.content import WRITER_ATTEMPTS, Writer, parse_draft, validate
from geo_blog.metadata_options import fields_to_fix, replace_metadata
from geo_blog.settings import Settings

KEYWORD = "AI workflow automation"
URLS = {"https://geo-insulation.com/", "https://example.com/docs", "https://example.org/research"}
GOOD = (
    "AI workflow automation works best with one clear handoff. Learn how to pick a task, "
    "check each result, and keep a person in control of every decision."
)


def message(text):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
        model_dump_json=lambda **kwargs: "{}",
    )


def long_description(article):
    fm, _ = parse_draft(article)
    broken = replace_metadata(article, {"description": fm["description"] + " Read the full guide."})
    assert len(parse_draft(broken)[0]["description"]) > 155
    return broken


def writer():
    w = Writer(Settings(_env_file=None, writer_patch_corrections=False), client=Mock())
    return w


def test_only_metadata_failures_qualify():
    def summary(*keys):
        return {"checks": [{"rule_key": k, "passed": False} for k in keys]}

    assert fields_to_fix(summary("description")) == ["description"]
    assert fields_to_fix(summary("title", "secondary_description")) == ["title", "description"]
    assert fields_to_fix(summary("description", "depth")) == []
    assert fields_to_fix({"checks": []}) == []


def test_replace_keeps_body_and_other_fields(valid_article):
    fixed = replace_metadata(valid_article, {"description": "New: value with a colon"})
    fm, body = parse_draft(fixed)
    assert fm["description"] == "New: value with a colon"
    assert fm["slug"] == "choose-first-job" and fm["keywords"] == [KEYWORD]
    assert body == parse_draft(valid_article)[1]
    assert valid_article.split("\n---\n", 1)[1] == fixed.split("\n---\n", 1)[1]


def test_description_over_limit_is_fixed_by_measured_option(tmp_path, valid_article):
    broken = long_description(valid_article)
    assert [c.rule_key for c in validate(broken, KEYWORD, URLS)[2].hard_failures] == ["description"]
    options = {"description": ["x" * 200, "AI workflow automation is short.", GOOD]}
    w = writer()
    w.call = Mock(
        side_effect=[
            message(broken),
            message(json.dumps(options)),
            message('{"verdict":"approve","notes":[]}'),
        ]
    )
    result = w.write_from_research(
        {"keyword": KEYWORD}, "2026-11-11", tmp_path, [], "Evidence", URLS
    )
    assert w.call.call_count == 3
    assert result["front_matter"]["description"] == GOOD
    assert result["report"]["passed"]
    assert w.call.call_args_list[1].kwargs["usage_stage"] == "metadata_options"
    assert json.loads((tmp_path / "metadata-chosen-0.json").read_text()) == {"description": GOOD}


def test_no_passing_option_uses_deterministic_repair(tmp_path, valid_article):
    broken = long_description(valid_article)
    w = writer()
    w.call = Mock(
        side_effect=[
            message(broken),
            message(json.dumps({"description": ["too short"]})),
            message('{"verdict":"approve","notes":[]}'),
        ]
    )
    result = w.write_from_research(
        {"keyword": KEYWORD}, "2026-11-11", tmp_path, [], "Evidence", URLS
    )
    assert w.call.call_count == 3
    assert 140 <= len(result["front_matter"]["description"]) <= 155
    assert result["report"]["passed"]


def test_body_failures_never_trigger_metadata_call(tmp_path, valid_article):
    short = valid_article.split("A clear task")[0] + "\n## A\n## B\n## C\n"
    w = writer()
    w.call = Mock(return_value=message(short))
    try:
        w.write_from_research({"keyword": KEYWORD}, "2026-11-11", tmp_path, [], "Evidence", URLS)
    except ValueError:
        pass
    stages = [c.kwargs.get("usage_stage") for c in w.call.call_args_list]
    assert "metadata_options" not in stages
    assert len(stages) == WRITER_ATTEMPTS


def test_writer_retains_generic_fallback_note_in_approved_payload(tmp_path, valid_article):
    broken = replace_metadata(valid_article, {"description": "AI workflow automation guide."})
    w = writer()
    w.call = Mock(
        side_effect=[
            message(broken),
            message(json.dumps({"description": ["too short"]})),
            message('{"verdict":"approve","notes":[]}'),
        ]
    )
    result = w.write_from_research(
        {"keyword": KEYWORD}, "2026-11-11", tmp_path, [], "Evidence", URLS
    )
    assert "generic last-resort" in result["metadata_fallback_note"]
    assert result["front_matter"]["description"] in result["metadata_fallback_note"]
    assert "last-resort" not in result["markdown"]
