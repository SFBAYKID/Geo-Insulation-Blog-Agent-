"""Regressions for the November 11, 2026 failure: impossible keyword pairs and hidden causes.

The cellulose topic needed 77 characters for both exact phrases in a 70-character H1,
and the alert printed only passing checks. These tests pin the fit rules to the live
queue's real keyword pairs and check that every failure names its real cause.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from geo_blog import weekly
from geo_blog.content import WRITER_ATTEMPTS, Writer, validate
from geo_blog.settings import Settings
from geo_blog.text_format import count_keyword_in_text
from geo_blog.writer_feedback import (
    DraftQualityError,
    exempt_secondary_fields,
    phrases_length,
    structure_feedback,
    topic_blockers,
)

# Primary and first supporting keyword of every Blog Posts row on November 11, 2026.
LIVE_QUEUE = [
    ("how hot does an attic get in texas", "Texas attic temperature"),
    ("cellulose vs fiberglass blown in insulation", "blown-in cellulose vs fiberglass"),
    ("why is my upstairs so hot even with ac", "upstairs hot downstairs cool"),
    ("wet attic insulation", "wet insulation after roof leak"),
    ("how long does attic insulation last", "attic insulation lifespan"),
]
URLS = {"https://geo-insulation.com/", "https://example.com/docs", "https://example.org/research"}


def article(primary, secondary, title, h1, description):
    """A structurally complete article built from real keywords."""
    fm = {
        "title": title,
        "slug": "test-article",
        "description": description,
        "keywords": [primary, secondary],
    }
    body = (
        f"# {h1}\n\n{primary.capitalize()} depends on the home. "
        f"This guide also covers {secondary}. "
        + "A careful assessment looks at the roof, the attic and the rooms below. "
        * 70
    )
    body += (
        "\n## Choose\n[Company](https://geo-insulation.com/)"
        "\n## Check\n[Docs](https://example.com/docs)"
        "\n## Review\n[Research](https://example.org/research)"
    )
    return "---\n" + yaml.safe_dump(fm, width=1000) + "---\n" + body


def pad(text, low=140, high=155):
    """Grow a description to the website window with neutral words."""
    filler = " Learn what to check first."
    while len(text) < low:
        text = text.rstrip(".") + filler
        filler = " Ask a pro."
    assert len(text) <= high, (len(text), text)
    return text


def failing_keys(text, primary, secondary):
    return {c.rule_key for c in validate(text, primary, URLS, secondary)[2].hard_failures}


@pytest.mark.parametrize(("primary", "secondary"), LIVE_QUEUE)
def test_every_live_topic_has_a_passing_shape(primary, secondary):
    """A natural draft exists for each live pair; no rule asks for the impossible."""
    exempt = exempt_secondary_fields(primary, secondary)
    title = primary.title()[:55]
    if "title" not in exempt:
        title = f"{primary.title()}: {secondary.title()}"
    h1 = primary.capitalize() + ("" if "h1" in exempt else f": {secondary}")
    description = pad(f"{primary.capitalize()}? Here is what {secondary} means for your home.")
    text = article(primary, secondary, title, h1, description)
    assert not topic_blockers(primary)
    assert len(title) <= 55 and len(h1) <= 70
    assert failing_keys(text, primary, secondary) == set()


def test_cellulose_h1_and_title_are_waived_but_description_and_body_are_not():
    primary, secondary = LIVE_QUEUE[1]
    assert phrases_length(primary, secondary) == 77
    assert exempt_secondary_fields(primary, secondary) == ["title", "h1"]
    description = pad(
        "Cellulose vs fiberglass blown in insulation: how blown-in cellulose vs fiberglass compare."
    )
    good = article(
        primary, secondary, "Cellulose vs Fiberglass Blown In Insulation", primary, description
    )
    assert failing_keys(good, primary, secondary) == set()
    no_secondary = good.replace(f"This guide also covers {secondary}. ", "").replace(
        "how blown-in cellulose vs fiberglass compare", "how two loose-fill materials compare"
    )
    assert {"secondary_description", "secondary_keyword"} <= failing_keys(
        no_secondary, primary, secondary
    )


def test_pairs_that_fit_still_require_the_secondary_everywhere():
    primary, secondary = LIVE_QUEUE[3]  # 52 characters together
    assert exempt_secondary_fields(primary, secondary) == []
    text = article(
        primary,
        secondary,
        "Wet Attic Insulation: What To Do",
        primary,
        pad("Wet attic insulation guide."),
    )
    assert {"secondary_title", "secondary_h1", "secondary_description"} <= failing_keys(
        text, primary, secondary
    )


def test_hyphen_and_space_are_the_same_phrase_but_words_stay_whole():
    assert count_keyword_in_text("Blown-in insulation", "blown in insulation") == 1
    assert count_keyword_in_text("blown in insulation", "blown-in insulation") == 1
    assert count_keyword_in_text("blown‑in insulation", "blown in insulation") == 1
    assert count_keyword_in_text("attics", "attic") == 0
    assert count_keyword_in_text("blowin insulation", "blown in insulation") == 0
    assert count_keyword_in_text("blown -- in", "blown in") == 0
    assert phrases_length("blown in insulation", "blown-in insulation") == 19


def test_only_an_impossible_primary_blocks_a_topic():
    assert topic_blockers("x" * 55) == []
    assert any("title" in b for b in topic_blockers("x" * 56))
    assert topic_blockers(" ") == ["Primary keyword is empty."]


def test_feedback_names_failures_and_waived_fields():
    primary, secondary = LIVE_QUEUE[1]
    summary = {
        "checks": [
            {"rule_key": "title", "passed": True},
            {"rule_key": "description", "passed": False},
        ]
    }
    text = structure_feedback(summary, {"title": "T", "description": "d" * 160}, primary, secondary)
    assert "Failed now: description\n" in text
    assert "do not fit together in the title, h1" in text
    assert "still required in: description, body, keyword metadata" in text


def message(text):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
        model_dump_json=lambda **kwargs: "{}",
    )


def test_exhausted_writer_raises_only_the_checks_still_failing(tmp_path, valid_article):
    broken = valid_article.replace("AI workflow automation: choose the first job", "X" * 59)
    writer = Writer(Settings(_env_file=None, writer_patch_corrections=False), client=Mock())
    writer.call = Mock(return_value=message(broken))
    with pytest.raises(DraftQualityError) as caught:
        writer.write_from_research(
            {"keyword": "AI workflow automation"}, "2026-11-11", tmp_path, [], "Evidence", URLS
        )
    assert writer.call.call_count == WRITER_ATTEMPTS
    error = caught.value
    assert isinstance(error, ValueError)
    assert error.blockers[0].startswith("title (Title <=55 characters; got 59)")
    assert all("passed" not in b and "customer_facing_copy" not in b for b in error.blockers)


def test_editor_and_reply_failures_are_named(tmp_path, valid_article):
    writer = Writer(Settings(_env_file=None, writer_patch_corrections=False), client=Mock())
    replies = [message(valid_article), message('{"verdict":"revise","notes":["Cite the source"]}')]
    replies += [message("not an article")] * (WRITER_ATTEMPTS - 1)
    writer.call = Mock(side_effect=replies)
    with pytest.raises(DraftQualityError) as caught:
        writer.write_from_research(
            {"keyword": "AI workflow automation"}, "2026-11-11", tmp_path, [], "Evidence", URLS
        )
    assert caught.value.blockers[0] == "Editor: Cite the source"
    assert caught.value.blockers[-1].startswith("Reply could not be applied")


def test_alert_lists_failed_checks_and_hold(monkeypatch):
    sent = []
    monkeypatch.setattr(weekly, "private_alert", lambda settings, text: sent.append(text))
    error = DraftQualityError("wet attic insulation", 7, ["title (Title <=55 characters; got 59)"])
    error.topic_held = "The topic is now Held."  # type: ignore[attr-defined]
    weekly.notify_failure(Mock(), error)
    assert "• title (Title <=55 characters; got 59)" in sent[0]
    assert "The topic is now Held." in sent[0]
    assert '"rule_key"' not in sent[0]


def test_link_addresses_never_count_as_keyword_text():
    linked = "Read [our guide](https://geo-insulation.com/blog/wet-attic-insulation/) first."
    assert count_keyword_in_text(linked, "wet attic insulation") == 0
    assert (
        count_keyword_in_text(
            "See https://example.com/wet-attic-insulation now", "wet attic insulation"
        )
        == 0
    )
    assert (
        count_keyword_in_text('[x](/blog/wet-attic-insulation "Wet")', "wet attic insulation") == 0
    )
    # The visible label still counts.
    labeled = (
        "Read [wet attic insulation tips](https://geo-insulation.com/blog/wet-attic-insulation/)."
    )
    assert count_keyword_in_text(labeled, "wet attic insulation") == 1


def test_link_alone_cannot_satisfy_the_intro_check():
    primary, secondary = LIVE_QUEUE[3]
    description = pad("Wet attic insulation after a roof leak: what to check first.")
    text = article(
        primary, secondary, "Wet Attic Insulation After a Roof Leak", primary, description
    )
    linked_only = text.replace(
        f"{primary.capitalize()} depends on the home.",
        "[Start here](https://geo-insulation.com/blog/wet-attic-insulation/) depends on the home.",
    )
    assert "keyword_intro" in failing_keys(linked_only, primary, secondary)


def test_keyword_metadata_accepts_hyphen_or_space():
    primary, secondary = LIVE_QUEUE[1]
    description = pad(
        "Cellulose vs fiberglass blown in insulation: how blown-in cellulose vs fiberglass compare."
    )
    text = article(
        primary, secondary, "Cellulose vs Fiberglass Blown In Insulation", primary, description
    )
    hyphenated = text.replace(
        "- blown-in cellulose vs fiberglass\n", "- blown in cellulose vs fiberglass\n"
    ).replace(f"- {primary}\n", "- cellulose vs fiberglass blown-in insulation\n")
    assert hyphenated != text
    assert failing_keys(hyphenated, primary, secondary) == set()
