"""Keep private production notes out of customer-facing articles and captions."""

from __future__ import annotations

import re

INTERNAL_COPY = re.compile(
    r"\b(?:Airtable|Vercel|GitHub|Claude|OpenAI|backend|source inventory|"
    r"publication_permission|privacy_review|keyword_record_ids|"
    r"(?:Geo|our|the) photo (?:library|catalog)|approved derivative|"
    r"no repair outcome is claimed|does not document a detailing treatment)\b",
    re.I,
)


def internal_copy_findings(text: str) -> list[str]:
    """Identify operational vocabulary, leaving ordinary home insulation terms untouched."""
    return sorted({match.group() for match in INTERNAL_COPY.finditer(text)})
