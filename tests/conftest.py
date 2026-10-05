"""Shared synthetic articles; tests never load local credentials."""

import pytest
import yaml


@pytest.fixture
def valid_article():
    fm = {
        "title": "AI workflow automation: choose the first job",
        "slug": "choose-first-job",
        "description": "AI workflow automation starts with one clear handoff. Learn how to choose a task, check the result, and keep a person in control of each decision.",
        "keywords": ["AI workflow automation"],
    }
    body = (
        "# AI workflow automation\n\nAI workflow automation starts with one clear handoff. "
        + "A clear task has inputs, actions, exceptions and a review step. " * 75
    )
    body += "\n## Choose\n[Company](https://geo-insulation.com/)\n## Check\n[Docs](https://example.com/docs)\n## Review\n[Research](https://example.org/research)"
    return "---\n" + yaml.safe_dump(fm) + "---\n" + body
