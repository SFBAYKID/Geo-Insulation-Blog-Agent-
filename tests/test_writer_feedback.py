"""Regressions for the November 4 metadata correction loop."""

from types import SimpleNamespace
from unittest.mock import Mock

from geo_blog.content import Writer, parse_draft, validate
from geo_blog.settings import Settings
from geo_blog.writer_feedback import secondary_fits_title, structure_feedback


def test_long_pair_title_exception_preserves_other_secondary_checks(valid_article):
    primary = "how hot does an attic get in texas"
    secondary = "Texas attic temperature"
    assert not secondary_fits_title(primary, secondary)
    assert secondary_fits_title("attic insulation", "insulation")
    assert secondary_fits_title("attic", "solar")
    article = valid_article.replace("AI workflow automation", primary)
    report = validate(article, primary, set(), secondary)[2]
    keys = {c.rule_key for c in report.checks if not c.passed}
    assert "secondary_title" not in keys
    assert {
        "secondary_h1",
        "secondary_description",
        "secondary_keyword",
        "secondary_metadata",
    } <= keys
    assert "secondary_title" in {
        c.rule_key
        for c in validate(valid_article, "AI workflow automation", set(), "attic")[2].checks
        if not c.passed
    }


def test_retry_receives_measured_values_and_still_requires_editor(
    tmp_path, valid_article, monkeypatch
):
    # Exercise the correction loop itself; metadata options are tested separately.
    monkeypatch.setattr("geo_blog.content.repair_metadata", lambda *a, **k: None)
    urls = {
        "https://geo-insulation.com/",
        "https://example.com/docs",
        "https://example.org/research",
    }
    metadata, _ = parse_draft(valid_article)
    broken = valid_article.replace(metadata["title"], "X" * 59)

    def message(text):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            model_dump_json=lambda **kwargs: "{}",
        )

    writer = Writer(Settings(_env_file=None, writer_patch_corrections=False), client=Mock())
    writer.call = Mock(
        side_effect=[
            message(broken),
            message(valid_article),
            message('{"verdict":"approve","notes":[]}'),
        ]
    )
    result = writer.write_from_research(
        {"keyword": "AI workflow automation"}, "2026-11-10", tmp_path, [], "Evidence", urls
    )
    correction = writer.call.call_args_list[1].args[1]
    assert '"characters_to_remove": 4' in correction
    assert '"value": "' + "X" * 59 in correction
    assert writer.call.call_count == 3
    assert result["report"]["passed"]
    assert not validate(broken, "AI workflow automation", urls)[2].passed


def test_description_feedback_has_exact_overage():
    feedback = structure_feedback(
        {"checks": []}, {"title": "Attic", "description": "x" * 157}, "attic", ""
    )
    assert '"characters_to_remove": 2' in feedback
    assert '"maximum": 155' in feedback
