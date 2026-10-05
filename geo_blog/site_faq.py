"""Move authored FAQs into the site's accordion without duplicating body content."""

from __future__ import annotations

import re
from typing import TypedDict


class Faq(TypedDict):
    """Plain-text contract shared by the website accordion and FAQ structured data."""

    question: str
    answer: str


def extract_faq(body: str) -> tuple[str, list[Faq]]:
    """Extract one explicit FAQ section; refuse ambiguous or lossy conversion."""
    sections = list(re.finditer(r"^## (?:Frequently Asked Questions|FAQs?)\s*$", body, re.M | re.I))
    if len(sections) != 1:
        raise ValueError("Provide exactly one ## Frequently Asked Questions section")
    start = sections[0]
    # A thematic break also ends the FAQ: writers put a closing call-to-action after
    # "---", and folding that linked copy into the last answer failed every retry
    # (September 25, 2026 blown-in insulation draft).
    following = re.search(r"^(?:## |-{3,}\s*$)", body[start.end() :], re.M)
    end = start.end() + following.start() if following else len(body)
    source = body[start.end() : end].strip()
    questions = list(re.finditer(r"^(?:\*\*(.+\?)\*\*|### (.+\?))\s*$", source, re.M))
    if not questions or source[: questions[0].start()].strip():
        raise ValueError("FAQ must contain bold or H3 questions followed by plain answers")
    faqs: list[Faq] = []
    for i, match in enumerate(questions):
        stop = questions[i + 1].start() if i + 1 < len(questions) else len(source)
        answer = source[match.end() : stop].strip()
        # The site contract is plain text: never silently strip links, lists or markup.
        if not answer or re.search(r"[*\[\]<>`#]|^\s*(?:- |\d+\. )", answer, re.M):
            raise ValueError(
                "FAQ answers must be nonempty plain text; keep citations in body prose"
            )
        question = match[1] or match[2]
        if any(faq["question"].casefold() == question.casefold() for faq in faqs):
            raise ValueError("Duplicate FAQ question")
        faqs.append({"question": question, "answer": " ".join(answer.split())})
    return body[: start.start()].rstrip() + "\n\n" + body[end:].lstrip(), faqs
