import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from geo_blog.content import editorial_review


def response(verdict, notes):
    text = json.dumps({"verdict": verdict, "notes": notes})
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
        model_dump_json=lambda **kwargs: json.dumps({"content": [{"type": "text", "text": text}]}),
    )


def test_contradictory_review_requires_fresh_explicit_model_approval(tmp_path):
    writer = Mock()
    writer.call.side_effect = [
        response("revise", ["All examples are accurate. No correction needed."]),
        response("approve", []),
    ]
    assert editorial_review(writer, "Unchanged article and evidence", tmp_path / "review.json") == {
        "verdict": "approve",
        "notes": [],
    }
    assert writer.call.call_count == 2
    assert writer.call.call_args.kwargs["cache_parts"] == ["Unchanged article and evidence"]
    assert (tmp_path / "review-response-1.json").exists()


def test_real_correction_is_not_discarded_when_mixed_with_praise(tmp_path):
    writer = Mock()
    writer.call.side_effect = [
        response("revise", ["Examples correct. No issue.", "Remove unsupported price."]),
        response("revise", ["Remove unsupported price."]),
    ]
    assert editorial_review(writer, "Article", tmp_path / "review.json") == {
        "verdict": "revise",
        "notes": ["Remove unsupported price."],
    }


@pytest.mark.parametrize(
    ("verdict", "notes"),
    [
        ("revise", ["No correction needed."]),
        ("approve", ["Remove unsupported price."]),
        ("revise", []),
    ],
)
def test_repeated_inconsistency_never_approves(tmp_path, verdict, notes):
    writer = Mock()
    writer.call.side_effect = [response(verdict, notes) for _ in range(3)]
    with pytest.raises(ValueError, match="inconsistent"):
        editorial_review(writer, "Article", tmp_path / "review.json")
    assert writer.call.call_count == 3


def test_legitimate_correction_is_returned_without_another_paid_call(tmp_path):
    writer = Mock()
    writer.call.return_value = response("revise", ["Remove unsupported price."])
    assert editorial_review(writer, "Article", tmp_path / "review.json")["verdict"] == "revise"
    writer.call.assert_called_once()


def test_truncated_review_retries_same_article_and_requires_complete_approval(tmp_path):
    truncated = response("approve", [])
    truncated.stop_reason = "max_tokens"
    writer = Mock()
    writer.call.side_effect = [truncated, response("approve", [])]
    assert (
        editorial_review(writer, "Original unchanged article", tmp_path / "review.json")["verdict"]
        == "approve"
    )
    assert writer.call.call_count == 2
    assert writer.call.call_args.kwargs["cache_parts"] == ["Original unchanged article"]


def test_truncated_reviews_fail_closed_at_fixed_limit(tmp_path):
    truncated = response("approve", [])
    truncated.stop_reason = "max_tokens"
    writer = Mock()
    writer.call.return_value = truncated
    with pytest.raises(ValueError, match="inconsistent"):
        editorial_review(writer, "Article", tmp_path / "review.json")
    assert writer.call.call_count == 3


def test_editor_keeps_required_keywords_without_extra_repetition():
    from pathlib import Path

    prompt = Path("geo_blog/prompts/editor.md").read_text()
    assert "deterministic code" in prompt
    assert "Do not review those again" in prompt
    assert "allowed_urls only" in prompt


def test_out_of_scope_notes_never_block_or_reach_the_writer(tmp_path):
    writer = Mock()
    writer.call.side_effect = [
        response(
            "revise",
            [
                "The meta description is 156 characters, exceeding the 140-155 limit.",
                "Add Article, FAQPage, and BreadcrumbList JSON-LD schema markup.",
            ],
        ),
        response("approve", ["The FAQ section uses the accordion, so this is correct."]),
    ]
    assert editorial_review(writer, "Article", tmp_path / "review.json") == {
        "verdict": "approve",
        "notes": [],
    }
    assert "never list them" in writer.call.call_args.args[1]
    mixed = Mock()
    mixed.call.return_value = response(
        "revise",
        ["Word count is 2,150, which meets the requirement.", "Remove the invented price."],
    )
    assert editorial_review(mixed, "Article", tmp_path / "mixed.json")["notes"] == [
        "Remove the invented price."
    ]


def test_duplicate_link_and_vague_verification_notes_are_out_of_scope():
    from geo_blog.editor_notes import required_notes

    notes = [
        "The AAA source is cited twice with its full link markdown in two paragraphs.",
        "Double-check no leftover unsupported size figures remain anywhere.",
        "Narrow the Tesla-specific creasing claim to Tesla homes.",
    ]
    assert required_notes(notes) == ["Narrow the Tesla-specific creasing claim to Tesla homes."]


def test_rereview_is_scoped_to_previous_notes():
    from geo_blog.editor_notes import rereview_request

    assert rereview_request([]) == "Review the supplied draft against its evidence."
    scoped = rereview_request(["Cite the AAA source for the insurer claim."])
    assert "Cite the AAA source" in scoped and "material factual" in scoped
