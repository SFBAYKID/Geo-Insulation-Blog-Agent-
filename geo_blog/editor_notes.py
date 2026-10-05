"""Drop editor notes that the editor prompt already places out of scope.

The editor is told that deterministic code owns lengths, counts and markup, and that
passed checks must not be listed. Haiku still returned such notes: on September 28,
2026 a blown-in insulation draft that passed every structure check was blocked by a
miscounted "156 characters" description note and a JSON-LD schema request (the
website renders schema), and the writer's "fix" then broke the keyword checks.
Filtering keeps real factual, safety and copy findings while removing those.
"""

from __future__ import annotations

import re

# Deterministic code enforces these; the website generates structured data.
OUT_OF_SCOPE = re.compile(
    r"\b\d+\s*(?:-\s*\d+\s*)?characters?\b|character (?:limit|count)|word count|"
    r"\b(?:schema|json-?ld|structured data|rich results)\b|"
    # unique_external_links already proves each URL is linked once (Sonnet 5 still
    # reported "cited twice" on passing drafts, September 28, 2026).
    r"(?:cited|linked|link(?:ed)?|appears|used) twice|only one hyperlink|link each external source url only once|"
    # The prompt forbids open-ended verification chores.
    r"\bdouble-check\b",
    re.I,
)
# Notes that report a passed check rather than request a change.
INFORMATIONAL = re.compile(
    r"no (?:correction|change|issue)s? (?:needed|required|found|here)|no issue\b|"
    r"requirements? (?:is |are )?(?:met|satisfied)|informational(?: only)?|disregard this note|"
    r"which is correct|so this is correct|satisf(?:y|ies|ying) the requirement|"
    r"meets the requirement|confirming compliance|so no change",
    re.I,
)


def required_notes(notes: list[str]) -> list[str]:
    """Return only notes that ask for an in-scope change."""
    return [n for n in notes if not OUT_OF_SCOPE.search(n) and not INFORMATIONAL.search(n)]


def rereview_request(prior_notes: list[str]) -> str:
    """Scope a correction pass to the previous findings plus material errors.

    A full fresh review of each corrected draft found new minor issues every round and
    never converged in seven passes (September 28, 2026).
    """
    if not prior_notes:
        return "Review the supplied draft against its evidence."
    listed = "\n".join("- " + note for note in prior_notes)
    return (
        "Re-review of a corrected draft. Your previous required notes were:\n"
        + listed
        + "\nReturn a note only when one of those notes is still unresolved, or when the draft "
        "contains a material factual, safety or legal error. Do not raise new wording, "
        "attribution-style, hedging or keyword-metadata preferences. If every previous note is "
        'resolved and no material error exists, return {"verdict":"approve","notes":[]}.'
    )
