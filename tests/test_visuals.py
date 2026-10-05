import json

import pytest
import yaml

from geo_blog.store import Store
from geo_blog.visuals import recent_visuals, validate_exercise
from geo_blog.website import export_preview


def exercise():
    return {
        "section": "Try a decision",
        "mode": "decision",
        "title": "What would you send?",
        "introduction": "A fictional example.",
        "scenario": "There is no confirmed date.",
        "options": [
            {"label": "Promise Friday", "explanation": "The date is not confirmed."},
            {"label": "Ask first", "explanation": "Check the date before promising."},
        ],
        "takeaway": "Find evidence before making a promise.",
    }


def test_exercise_export_preserves_choices_and_requires_section(tmp_path):
    valid_article = "---\ntitle: Example\nslug: example\ndescription: Example article\nkeywords: [example]\n---\n\n# Example\n\nBody."
    st = Store(tmp_path / "state")
    st.reserve("one", "day", "topic")
    st.save("one", {"markdown": valid_article, "topic": {"exercise": exercise()}})
    with pytest.raises(ValueError, match="matching Markdown section"):
        export_preview(st, "one", tmp_path / "site")
    with st.db() as db:
        draft = json.loads(st.get("one")["payload"])
        draft["markdown"] += "\n\n## Try a decision\n\nA fictional example.\n"
        db.execute("UPDATE drafts SET payload=? WHERE id=?", (json.dumps(draft), "one"))
    export_preview(st, "one", tmp_path / "site")
    path = next((tmp_path / "site/src/content/blog").glob("*/*.mdx"))
    meta = yaml.safe_load(path.read_text().split("---")[1])
    assert meta["exercise"] == exercise()


def test_exercise_rejects_unsupported_format_and_unbounded_choices():
    e = exercise()
    validate_exercise(e)
    e["mode"] = "arbitrary-code"
    with pytest.raises(ValueError):
        validate_exercise(e)
    e["mode"] = "decision"
    e["options"] *= 3
    with pytest.raises(ValueError):
        validate_exercise(e)


def test_oversized_exercise_field_is_correctable_feedback_not_a_failed_run():
    """An over-long choice label ended the September 17 run instead of retrying the planner."""
    from geo_blog.visuals import VisualReviewError

    e = exercise()
    e["options"][1]["label"] = "Ask the customer to confirm the fix before closing " * 5
    with pytest.raises(VisualReviewError, match="choice label: 255 characters"):
        validate_exercise(e)
    e = exercise()
    e["scenario"] = "x" * 1401
    with pytest.raises(VisualReviewError, match="scenario"):
        validate_exercise(e)
    e = exercise()
    e["options"][0]["explanation"] = " "
    with pytest.raises(VisualReviewError, match="choice explanation"):
        validate_exercise(e)


def test_recent_visuals_ignores_current_article(tmp_path):
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "visual-plan.json").write_text(
        json.dumps({"art": "email", "exercise": {"mode": "decision"}})
    )
    current = tmp_path / "current"
    current.mkdir()
    (current / "visual-plan.json").write_text("{}")
    assert recent_visuals(current) == [{"art": "email", "exercise": {"mode": "decision"}}]


def test_visual_retry_passes_findings_to_planner_and_keeps_existing_prose(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from anthropic.types import Message

    from geo_blog import visuals

    (tmp_path / "company.json").write_text("[]")
    raw = Message(
        id="test",
        type="message",
        role="assistant",
        model="test",
        stop_reason="end_turn",
        content=[{"type": "text", "text": "Evidence"}],
        usage={"input_tokens": 1, "output_tokens": 1},
    )
    (tmp_path / "research.json").write_text(raw.model_dump_json())
    draft = {
        "markdown": "Already approved prose",
        "topic": {"keyword": "example"},
        "sources": [],
    }
    revised = dict(draft, markdown="Narrowly corrected prose")
    writer = Mock()
    writer.write_from_research.return_value = revised
    planner = Mock(
        side_effect=[
            visuals.VisualReviewError("Make the evidence check explicit"),
            revised,
        ]
    )
    monkeypatch.setattr(visuals, "add_visuals", planner)
    assert visuals.complete_visuals(writer, draft, tmp_path, "2026-09-17") == revised
    writer.write_from_research.assert_not_called()
    assert planner.call_args_list[1].args[1]["markdown"] == "Already approved prose"
    assert planner.call_args_list[1].args[3] == "Make the evidence check explicit"


def test_visual_retry_keeps_earlier_corrections(tmp_path, monkeypatch):
    """September 22, 2026: the third plan undid the second plan's format fix because only
    the latest correction (a length) was passed along."""
    from unittest.mock import Mock

    from anthropic.types import Message

    from geo_blog import visuals

    (tmp_path / "company.json").write_text("[]")
    raw = Message(
        id="test",
        type="message",
        role="assistant",
        model="test",
        stop_reason="end_turn",
        content=[{"type": "text", "text": "Evidence"}],
        usage={"input_tokens": 1, "output_tokens": 1},
    )
    (tmp_path / "research.json").write_text(raw.model_dump_json())
    draft = {
        "markdown": "Approved prose",
        "topic": {"keyword": "example"},
        "sources": [],
    }
    planner = Mock(
        side_effect=[
            visuals.VisualReviewError("Use a different mode"),
            visuals.VisualReviewError("Explanation too long"),
            draft,
        ]
    )
    monkeypatch.setattr(visuals, "add_visuals", planner)
    visuals.complete_visuals(Mock(), draft, tmp_path, "2026-09-22")
    assert "Use a different mode" in planner.call_args_list[2].args[3]
    assert "Explanation too long" in planner.call_args_list[2].args[3]
