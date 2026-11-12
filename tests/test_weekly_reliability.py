"""Offline regressions for length repair, advisory checks and topic fallthrough."""

import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from geo_blog.content import parse_draft, validate
from geo_blog.metadata_lengths import fit_length
from geo_blog.metadata_options import deterministic_repair, replace_metadata
from geo_blog.settings import Settings
from geo_blog.store import Store
from geo_blog.weekly_attempts import draft_with_fallback, report_soft_findings
from geo_blog.writer_feedback import DraftQualityError

URLS = {"https://geo-insulation.com/", "https://example.com/docs", "https://example.org/research"}


def test_cached_november_four_draft_repairs_without_provider():
    folder = Path(__file__).parent / "fixtures/failed-drafts/735a09f5d92b480d"
    topic = json.loads((folder / "topic.json").read_text())
    text = (folder / "latest-draft.md").read_text()
    urls = set(re.findall(r"\]\((https?://[^\s)]+)\)", text))
    result = deterministic_repair(validate, text, topic, urls)
    assert result is not None
    assert result[1][2].passed
    assert len(result[1][0]["title"]) <= 55


@pytest.mark.parametrize(
    ("draft_id", "failed_rule"),
    [("5bb96eda603547a9", "h1"), ("34ec3886d7c84b1a", "description")],
)
def test_cached_november_eleven_drafts_repair_without_provider(draft_id, failed_rule):
    from geo_blog.metadata_lengths import repair_heading

    folder = Path(__file__).parent / "fixtures/failed-drafts" / draft_id
    topic = json.loads((folder / "topic.json").read_text())
    text = (folder / "latest-draft.md").read_text()
    urls = set(re.findall(r"\]\((https?://[^\s)]+)\)", text))
    original = validate(text, topic["keyword"], urls, topic["secondary_keyword"])[2]
    assert {c.rule_key for c in original.checks if c.is_hard and not c.passed} == {failed_rule}
    heading_fixed = repair_heading(text, topic["keyword"])
    result = deterministic_repair(validate, heading_fixed, topic, urls)
    assert result is not None and result[1][2].passed
    fm, body = parse_draft(result[0])
    assert 140 <= len(fm["description"]) <= 155
    assert len(re.search(r"^# (.+)$", body, re.M)[1]) <= 70
    assert body == parse_draft(heading_fixed)[1]
    assert re.sub(r"^# .+$", "#", body, flags=re.M) == re.sub(
        r"^# .+$", "#", parse_draft(text)[1], flags=re.M
    )


def test_trim_retains_phrases_even_at_end():
    value = "A detailed homeowner comparison and guide to cellulose vs fiberglass insulation"
    repaired = fit_length(value, ["cellulose vs fiberglass insulation"], 20, 55)
    assert repaired is not None and len(repaired) <= 55
    assert "cellulose vs fiberglass insulation" in repaired
    assert fit_length("x" * 56, ["x" * 56], 20, 55) is None


def test_description_158_chars_is_repaired_offline(valid_article):
    fm, _ = parse_draft(valid_article)
    text = replace_metadata(valid_article, {"description": fm["description"] + " Learn more."})
    assert len(parse_draft(text)[0]["description"]) == 158
    result = deterministic_repair(validate, text, {"keyword": "AI workflow automation"}, URLS)
    assert result is not None and result[1][2].passed
    assert 140 <= len(result[1][0]["description"]) <= 155
    assert parse_draft(text)[1] == parse_draft(result[0])[1]


def test_only_named_rules_are_advisory(valid_article):
    report = validate(valid_article, "AI workflow automation", URLS, "supporting phrase")[2]
    assert report.passed
    for check in report.checks:
        assert check.is_hard == (
            not check.rule_key.startswith("secondary_")
            and check.rule_key not in {"keyword_h1", "repetition"}
        )


def quality_error(topic):
    error = DraftQualityError(topic, 7, ["depth"])
    error.topic_id = topic
    return error


def test_weekly_skips_failed_topics_and_uses_unique_keys(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=tmp_path)
    alert = Mock()
    monkeypatch.setattr("geo_blog.weekly.private_alert", alert)
    daily = Mock(side_effect=[quality_error("first"), quality_error("second"), "ready"])
    assert draft_with_fallback(settings, Store(tmp_path), daily) == "ready"
    calls = daily.call_args_list
    assert len({c.kwargs["request_key"] for c in calls}) == 3
    assert [c.kwargs["excluded_topics"] for c in calls] == [set(), {"first"}, {"first", "second"}]
    assert alert.call_count == 2


def test_weekly_caps_failures_and_never_retries_other_errors(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=tmp_path)
    monkeypatch.setattr("geo_blog.weekly.private_alert", Mock())
    daily = Mock(side_effect=[quality_error(str(i)) for i in range(3)])
    with pytest.raises(DraftQualityError):
        draft_with_fallback(settings, Store(tmp_path), daily)
    assert daily.call_count == 3
    daily = Mock(side_effect=RuntimeError("ambiguous provider result"))
    with pytest.raises(RuntimeError):
        draft_with_fallback(settings, Store(tmp_path), daily)
    assert daily.call_count == 1


def test_advisory_note_only_uses_private_channel(tmp_path, monkeypatch, valid_article):
    settings = Settings(_env_file=None, storage_dir=tmp_path, slack_test_channel_id="C0B02721MNK")
    client = Mock()
    monkeypatch.setattr("geo_blog.slack_guard.safe_client", lambda *args: client)
    report = validate(valid_article, "AI workflow automation", URLS, "supporting phrase")[2]
    report_soft_findings(settings, {"report": report.summary})
    call = client.chat_postMessage.call_args
    assert call.kwargs["channel"] == "C0B02721MNK"
    assert "secondary_description" in call.kwargs["text"]


def test_overlong_h1_is_shortened_before_validation(valid_article):
    from geo_blog.metadata_lengths import repair_heading

    text = valid_article.replace(
        "# AI workflow automation\n", "# AI workflow automation" + " guide" * 12 + "\n"
    )
    fixed = repair_heading(text, "AI workflow automation")
    assert validate(fixed, "AI workflow automation", URLS)[2].passed
    assert "AI workflow automation" in re.search(r"^# (.+)$", fixed, re.M)[1]
    assert repair_heading(valid_article, "AI workflow automation") == valid_article


def test_neutral_description_fallback_covers_feasible_phrase_lengths():
    from geo_blog.metadata_lengths import description_fallback

    for length in range(1, 121):
        phrase = "x" * length
        result = description_fallback([phrase])
        assert 140 <= len(result) <= 155 and phrase in result


def test_short_description_gets_measured_neutral_fallback(valid_article):
    text = replace_metadata(valid_article, {"description": "AI workflow automation guide."})
    result = deterministic_repair(validate, text, {"keyword": "AI workflow automation"}, URLS)
    assert result is not None and result[1][2].passed


def test_monday_cli_uses_explicit_environment_and_staging(monkeypatch, tmp_path):
    from geo_blog import cli

    settings = Settings(_env_file=None, storage_dir=tmp_path)
    factory = Mock(return_value=settings)
    stage = Mock(return_value="staged")
    monkeypatch.setattr(cli, "Settings", factory)
    monkeypatch.setattr("geo_blog.weekly.stage_test", stage)
    monkeypatch.setattr(
        "sys.argv", ["geo_blog.cli", "--env-file", "/explicit/geo.env", "weekly", "--test"]
    )
    cli.main()
    factory.assert_called_once_with(_env_file="/explicit/geo.env")
    assert stage.call_count == 1


def test_generic_fallback_is_saved_and_reported_privately(tmp_path, monkeypatch, valid_article):
    from geo_blog.metadata_options import generic_description_note

    text = replace_metadata(valid_article, {"description": "AI workflow automation guide."})
    result = deterministic_repair(
        validate, text, {"keyword": "AI workflow automation"}, URLS, tmp_path
    )
    description = result[1][0]["description"]
    note = generic_description_note(tmp_path, description)
    assert "generic last-resort" in note and description in note
    assert generic_description_note(tmp_path, "A later model rewrite") == ""
    client = Mock()
    monkeypatch.setattr("geo_blog.slack_guard.safe_client", lambda *args: client)
    report_soft_findings(Settings(_env_file=None), {"metadata_fallback_note": note})
    call = client.chat_postMessage.call_args
    assert call.kwargs["channel"] == "C0B02721MNK"
    assert note in call.kwargs["text"]
