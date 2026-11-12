"""Ensure FAQ extraction preserves answers and surrounding copy without duplication."""

import pytest

from geo_blog.site_faq import extract_faq


def test_explicit_faq_moves_only_authored_pairs():
    body = "# Title\n\n## Ordinary question?\n\nBody.\n\n## Frequently Asked Questions\n\n**First?**\n\nAnswer one.\n\n### Second?\n\nAnswer two.\n\n## Contact\n\nCall us."
    prose, faq = extract_faq(body)
    assert "Ordinary question?" in prose and "Call us." in prose
    assert "First?" not in prose and "Answer two." not in prose
    assert faq == [
        {"question": "First?", "answer": "Answer one."},
        {"question": "Second?", "answer": "Answer two."},
    ]


def test_closing_call_to_action_after_rule_stays_in_body():
    body = "# Title\n\n## FAQ\n\n**Last?**\nPlain answer.\n\n---\n\n[Get a Free Estimate](https://geo-insulation.com/contact)"
    prose, faq = extract_faq(body)
    assert faq == [{"question": "Last?", "answer": "Plain answer."}]
    assert prose.endswith("---\n\n[Get a Free Estimate](https://geo-insulation.com/contact)")


def test_unseparated_closing_link_explains_lossless_repair():
    closing = "Review [Geo's service](https://geo-insulation.com/attic-insulation-in-san-antonio/)."
    body = "## FAQ\n\n**Is it safe?**\n\nAsk a professional.\n\n" + closing
    with pytest.raises(ValueError, match="## Next steps") as caught:
        extract_faq(body)
    assert "Is it safe?" in str(caught.value)
    prose, faq = extract_faq(body.replace(closing, "## Next steps\n\n" + closing))
    assert faq == [{"question": "Is it safe?", "answer": "Ask a professional."}]
    assert closing in prose


@pytest.mark.parametrize(
    "body",
    [
        "# Title\n\n## Ordinary?\n\nBody",
        "## FAQ\n\n**Empty?**",
        "## FAQ\n\n**Linked?**\n\n[Source](https://example.com)",
        "## FAQ\n\n**Same?**\n\nOne.\n\n**Same?**\n\nTwo.",
        "## FAQ\n\n**One?**\n\nAnswer.\n\n## FAQ\n\n**Two?**\n\nAnswer.",
    ],
)
def test_ambiguous_faq_fails_instead_of_losing_copy(body):
    with pytest.raises(ValueError):
        extract_faq(body)


def test_repeated_external_source_is_a_blocking_finding():
    from geo_blog.content import validate

    markdown = """---
title: Car care
slug: car-care
description: A summary.
keywords: [car care]
---
# Car care

[First](https://example.org/a) and [again](https://example.org/a).
"""
    _, _, report = validate(markdown, "car care", {"https://example.org/a"})
    finding = next(check for check in report.checks if check.rule_key == "unique_external_links")
    assert not finding.passed
