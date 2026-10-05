"""Customer copy must not disclose private image or publishing operations."""

from geo_blog.content import validate
from geo_blog.public_copy import internal_copy_findings


def test_internal_caption_blocks_draft(valid_article):
    report = validate(
        valid_article + "\n\nA home exterior from the Geo photo library.",
        "AI workflow automation",
        set(),
    )[2]
    assert "customer_facing_copy" in {c.rule_key for c in report.hard_failures}


def test_literal_customer_caption_is_allowed():
    assert not internal_copy_findings("Silver car with front insulation and fender damage.")
    assert internal_copy_findings("This photo does not document a detailing treatment.")
