"""Parse source keyword rows and plan conservative, repeatable Airtable changes."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

HEADERS = [
    "Keyword",
    "Search Volume",
    "Difficulty",
    "Priority",
    "GSC Position",
    "GSC Impressions",
    "Target Page",
    "On-Page Target",
    "Notes",
    "Client Notes",
]
FIELDS = HEADERS + ["Topic", "Topic Order"]
Values = dict[str, str | int | float | None]


def text(value: object) -> str:
    """Preserve source content while trimming accidental outer whitespace."""
    return "" if value is None else str(value).strip()


def key(fields: Values) -> str:
    """Use keyword plus page context, never a mutable row number, as identity."""
    keyword = " ".join(unicodedata.normalize("NFKC", text(fields.get("Keyword"))).split())
    target = text(fields.get("Target Page"))
    match = re.fullmatch(r"\(create page at (.+)\)", target, re.I)
    if match:
        target = match[1].strip()
    target = target.rstrip("/") or "/"
    return json.dumps([keyword.casefold(), target], ensure_ascii=False)


def parse_rows(rows: list[list[object]]) -> list[Values]:
    """Validate the named columns and numbered topic headings; fail on malformed rows."""
    if not rows or [text(v) for v in rows[0][:10]] != HEADERS:
        raise ValueError("Source column headings changed; review the mapping")
    result: list[Values] = []
    topic, order = "", 0
    keys: set[str] = set()
    for number, raw in enumerate(rows[1:], 2):
        values = list(raw[:10]) + [None] * max(0, 10 - len(raw))
        if not any(text(v) for v in values):
            continue
        heading = re.fullmatch(r"(\d+)\.\s+(.+)", text(values[0]))
        if heading and not any(text(v) for v in values[1:]):
            order, topic = int(heading[1]), heading[2]
            continue
        if not topic or not text(values[0]) or not text(values[6]):
            raise ValueError(f"Incomplete keyword identity or topic at source row {number}")
        record: Values = {name: text(value) or None for name, value in zip(HEADERS, values)}
        for name in ("Search Volume", "GSC Position", "GSC Impressions"):
            value = record[name]
            if value is not None:
                numeric = float(str(value).replace(",", ""))
                if not 0 <= numeric < 1e12:
                    raise ValueError(f"Invalid {name} at row {number}")
                record[name] = int(numeric) if numeric.is_integer() else numeric
        record.update({"Topic": topic, "Topic Order": order})
        identity = key(record)
        if identity in keys:
            raise ValueError(f"Duplicate keyword and target page at source row {number}")
        keys.add(identity)
        result.append(record)
    if not result:
        raise ValueError("Empty source: refusing to sync")
    return result


@dataclass
class Plan:
    """Changes and retained source history; conflicts prevent the entire write pass."""

    creates: list[Values] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unchanged: int = 0


def normalize(fields: dict[str, Any], types: dict[str, str]) -> Values:
    """Coerce only the mapped source fields to the observed Airtable schema."""
    result: Values = {}
    for name in FIELDS:
        value = fields.get(name)
        if value is None or value == "":
            result[name] = None
        elif types[name] in {"number", "currency", "percent"}:
            n = float(str(value).replace(",", ""))
            if not 0 <= n < 1e12:
                raise ValueError(f"Invalid numeric field: {name}")
            result[name] = int(n) if n.is_integer() else n
        else:
            result[name] = text(value)
    return result


def build_plan(
    source: list[Values],
    records: list[dict[str, Any]],
    previous: dict[str, Values],
    types: dict[str, str],
) -> Plan:
    """Compare field values and protect manual edits, identities and existing records."""
    plan = Plan()
    existing: dict[str, dict[str, Any]] = {}
    for existing_record in records:
        fields = existing_record["fields"]
        if not text(fields.get("Keyword")):
            continue
        identity = key(fields)
        if identity in existing:
            plan.conflicts.append(f"Duplicate Airtable identity: {identity}")
        existing[identity] = existing_record
    incoming = {key(row): normalize(row, types) for row in source}
    if len(incoming) != len(source):
        plan.conflicts.append("Duplicate source identities")
    plan.missing = sorted(set(existing) - set(incoming))
    # Without an immutable source ID, a rename and a new row can look identical.
    # Hold that combination for review instead of manufacturing a duplicate.
    if (set(previous) - set(incoming)) and (set(incoming) - set(previous)):
        plan.conflicts.append("Possible keyword/page rename: source has both removed and new keys")
    for identity, row in incoming.items():
        record = existing.get(identity)
        if record is None:
            if identity in previous:
                plan.conflicts.append(f"Previously synced Airtable record missing: {identity}")
            else:
                plan.creates.append(row)
            continue
        current = normalize(record["fields"], types)
        baseline = previous.get(identity)
        changes: Values = {}
        for name, wanted in row.items():
            if wanted == current[name]:
                continue
            if baseline is not None:
                if wanted == baseline.get(name):
                    continue  # Manual Airtable override; source did not change.
                if current[name] != baseline.get(name):
                    plan.conflicts.append(f"Both sources edited {identity}: {name}")
                    continue
            changes[name] = wanted
        if changes:
            plan.updates.append({"id": record["id"], "fields": changes})
        else:
            plan.unchanged += 1
    return plan
