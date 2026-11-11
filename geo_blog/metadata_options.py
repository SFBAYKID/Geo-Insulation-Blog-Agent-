"""Fix title/description length by choosing among measured options, not by recounting.

Why this exists: language models cannot count characters. On November 11, 2026 the
cellulose rerun produced a complete article whose only failing check was the
description length. Each correction trimmed a few characters (176, 176, 170, 164,
158) and the run ran out of attempts three characters over the 155 limit.

When every failing check is a title or description check, ask once for several
alternative wordings, then measure each one in code with the full validator and keep
the first that passes. Selection is deterministic; the model only has to produce
variety. If no option passes, a provider-free word-boundary repair is validated before
returning to the normal correction loop.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .writer_feedback import (
    DESCRIPTION_MAX,
    DESCRIPTION_MIN,
    TITLE_MAX,
    failed_checks,
    secondary_required_in,
)

OPTIONS = 10
# Checks a metadata-only rewrite can fix without touching the article body.
FIELD_CHECKS = {
    "title": {"title", "keyword_title", "secondary_title"},
    "description": {"description", "keyword_description", "secondary_description"},
}
LIMITS = {"title": (20, TITLE_MAX), "description": (DESCRIPTION_MIN, DESCRIPTION_MAX)}
FORMAT_FIELD = {"type": "array", "items": {"type": "string"}}


def fields_to_fix(summary: dict[str, Any]) -> list[str]:
    """Metadata fields to rewrite, or [] when any failure lies outside them."""
    failing = {str(c.get("rule_key")) for c in failed_checks(summary)}
    if not failing or not failing <= set().union(*FIELD_CHECKS.values()):
        return []
    return [field for field, keys in FIELD_CHECKS.items() if failing & keys]


def request_text(fields: list[str], primary: str, secondary: str) -> str:
    """Ask for variety around the exact window; the code does the measuring."""
    lines = [
        "Only the article metadata below fails its checks; the article body is approved "
        "as is. Do not rewrite the article. Write fresh alternatives for these fields.",
    ]
    for field in fields:
        low, high = LIMITS[field]
        phrases = [f'"{primary}"']
        if secondary_required_in(field, primary, secondary):
            phrases.append(f'"{secondary}"')
        lines.append(
            f"- {field}: {OPTIONS} different options, each {low}-{high} characters including "
            f"spaces. Each must contain the exact phrase(s) {' and '.join(phrases)}. Make "
            "their lengths differ: some short, some near the limit. Plain text, one "
            "sentence or headline, no quotation marks, no line breaks, accurate to the article."
        )
    lines.append("Return JSON only: " + json.dumps({f: ["option", "..."] for f in fields}) + ".")
    return "\n".join(lines)


def output_format(fields: list[str]) -> dict[str, Any]:
    """Strict JSON schema so the reply is always parseable."""
    return {
        "format": {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {f: FORMAT_FIELD for f in fields},
                "required": fields,
                "additionalProperties": False,
            },
        }
    }


def replace_metadata(text: str, values: dict[str, str]) -> str:
    """Swap front-matter fields while keeping the article body byte-for-byte."""
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text.strip(), re.S)
    if not match:
        raise ValueError("Draft has no front matter")
    fm = yaml.safe_load(match[1])
    fm.update(values)
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=10_000)
    return "---\n" + dumped + "---\n" + match[2]


def candidates(reply: dict[str, Any], field: str) -> list[str]:
    """Clean, de-duplicated single-line options, shortest-first within the window."""
    low, high = LIMITS[field]
    seen: list[str] = []
    for value in reply.get(field, []) if isinstance(reply.get(field), list) else []:
        cleaned = " ".join(str(value).split()).strip().strip('"“”')
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    # Measured order: in-window options first, nearest the middle of the window.
    middle = (low + high) / 2
    return sorted(seen, key=lambda v: (not low <= len(v) <= high, abs(len(v) - middle)))


def repair_metadata(
    writer: Any,
    validate: Any,
    text: str,
    topic: dict[str, Any],
    urls: set[str],
    summary: dict[str, Any],
    context: str,
    output_dir: Path,
    attempt: int,
) -> tuple[str, Any] | None:
    """Return (text, validate result) for the first option set that passes, else None."""
    fields = fields_to_fix(summary)
    if not fields:
        return None
    primary = topic["keyword"]
    secondary = topic.get("secondary_keyword", "")
    from .content import response_json

    response = writer.call(
        "You write accurate, natural metadata for a homeowner insulation article.",
        request_text(fields, primary, secondary),
        max_tokens=2000,
        usage_stage="metadata_options",
        cache_parts=[context, "Draft:\n" + text],
        output_config=output_format(fields),
    )
    (output_dir / f"metadata-options-{attempt}.json").write_text(response.model_dump_json(indent=2))
    try:
        reply = response_json(response)
    except ValueError:
        return deterministic_repair(validate, text, topic, urls, output_dir)
    if not isinstance(reply, dict):
        return deterministic_repair(validate, text, topic, urls, output_dir)
    options = {f: candidates(reply, f) for f in fields}
    if not all(options.values()):
        return deterministic_repair(validate, text, topic, urls, output_dir)
    # Try each field's options with the others fixed at their first choice, then all
    # pairs; at most OPTIONS * OPTIONS cheap local validations.
    combos: list[dict[str, str]] = []
    if len(fields) == 1:
        combos = [{fields[0]: v} for v in options[fields[0]]]
    else:
        combos = [
            {"title": t, "description": d} for t in options["title"] for d in options["description"]
        ]
    for values in combos:
        try:
            fixed = replace_metadata(text, values)
            result = validate(fixed, primary, urls, secondary)
        except (ValueError, TypeError, KeyError, yaml.YAMLError):
            continue
        if result[2].passed:
            (output_dir / f"metadata-chosen-{attempt}.json").write_text(
                json.dumps(values, indent=2)
            )
            return fixed, result
    return deterministic_repair(validate, text, topic, urls, output_dir)


def deterministic_repair(
    validate: Any,
    text: str,
    topic: dict[str, Any],
    urls: set[str],
    output_dir: Path | None = None,
) -> tuple[str, Any] | None:
    """Repair lengths locally while retaining every required metadata phrase."""
    from .content import parse_draft
    from .metadata_lengths import description_fallback, fit_length

    primary = topic["keyword"]
    secondary = topic.get("secondary_keyword", "")
    fm, _ = parse_draft(text)
    values = {}
    generic_description = None
    for field, (low, high) in LIMITS.items():
        phrases = [primary]
        if secondary_required_in(field, primary, secondary):
            from .text_format import count_keyword_in_text

            if count_keyword_in_text(fm[field], secondary):
                phrases.append(secondary)
        if not low <= len(fm[field]) <= high:
            fixed = fit_length(fm[field], phrases, low, high)
            if fixed is None:
                fixed = description_fallback(phrases) if field == "description" else primary
                if field == "description":
                    generic_description = fixed
            values[field] = fixed
    fixed_text = replace_metadata(text, values) if values else text
    result = validate(fixed_text, primary, urls, secondary)
    if result[2].passed and generic_description is not None and output_dir is not None:
        (output_dir / "generic-description.json").write_text(
            json.dumps({"description": generic_description})
        )
    return (fixed_text, result) if result[2].passed else None


def generic_description_note(output_dir: Path, description: str) -> str:
    """Flag only a fallback still present in the final editor-approved draft."""
    path = output_dir / "generic-description.json"
    if not path.exists() or json.loads(path.read_text()).get("description") != description:
        return ""
    return (
        "The description uses generic last-resort wording because model options and "
        "word-boundary trimming did not fit. The editor approved it, but it needs "
        "owner attention: " + description
    )
