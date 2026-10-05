"""Regression checks for incremental updates, identity conflicts and crash safety."""

import copy
import json

import pytest

from tools import keyword_sync as runtime
from tools.keyword_sync_model import FIELDS, HEADERS, build_plan, key, normalize, parse_rows

TYPES = {name: "multilineText" for name in FIELDS}
TYPES.update({"Search Volume": "number", "Topic Order": "number"})


def sample(keyword="air sealing", target="/services/air-leaks", volume=0):
    return parse_rows(
        [
            HEADERS,
            ["1. Air Sealing"],
            [
                keyword,
                volume,
                "Low",
                "Tier 1",
                None,
                None,
                target,
                "Air Sealing",
                "Original note",
                None,
            ],
        ]
    )[0]


def record(row, rid="rec1"):
    return {"id": rid, "fields": {**normalize(row, TYPES), "Publication Status": "Published"}}


def test_parse_blank_zero_and_duplicate_identity():
    row = sample()
    assert row["Search Volume"] == 0
    assert row["GSC Position"] is None
    assert key(sample(target="(create page at /services/air-leaks)")) == key(row)
    with pytest.raises(ValueError, match="Duplicate"):
        parse_rows(
            [
                HEADERS,
                ["1. Air Sealing"],
                ["a", 1, None, None, None, None, "/a"],
                [" A ", 1, None, None, None, None, "/a/"],
            ]
        )


def test_same_keyword_distinct_pages_and_unchanged():
    a, b = sample(), sample(target="/guides/air-leaks")
    plan = build_plan([a, b], [record(a), record(b, "rec2")], {}, TYPES)
    assert plan.unchanged == 2
    assert not plan.creates and not plan.updates and not plan.conflicts


def test_changed_field_only_and_manual_edit_preserved():
    before = sample()
    after = {**before, "Search Volume": 100}
    current = record(before)
    current["fields"]["Client Notes"] = "Keep this manual note"
    plan = build_plan([after], [current], {key(before): normalize(before, TYPES)}, TYPES)
    assert plan.updates == [{"id": "rec1", "fields": {"Search Volume": 100}}]
    assert not plan.conflicts


def test_conflicting_edit_blocks_and_removed_row_retained():
    before = sample()
    after = {**before, "Notes": "Source changed"}
    current = record(before)
    current["fields"]["Notes"] = "Human changed"
    plan = build_plan([after], [current], {key(before): normalize(before, TYPES)}, TYPES)
    assert plan.conflicts
    other = sample("other")
    plan = build_plan([other], [record(before), record(other, "rec2")], {}, TYPES)
    assert plan.missing == [key(before)]
    assert not plan.creates and not plan.updates


def test_rename_or_deleted_airtable_row_is_not_recreated():
    before = sample()
    previous = {key(before): normalize(before, TYPES)}
    assert build_plan([sample("renamed")], [record(before)], previous, TYPES).conflicts
    plan = build_plan([before], [], previous, TYPES)
    assert plan.conflicts and not plan.creates
    assert build_plan([before], [record(before), record(before, "rec2")], {}, TYPES).conflicts


class FakeAPI:
    base = "app"
    table = "tbl"

    def __init__(self, rows, ambiguous=False):
        self.rows = copy.deepcopy(rows)
        self.writes = []
        self.ambiguous = ambiguous

    def schema(self):
        return TYPES

    def records(self):
        return copy.deepcopy(self.rows)

    def request(self, method, path, **kwargs):
        self.writes.append((method, kwargs["json"]))
        if self.ambiguous:
            raise TimeoutError("unknown result")
        payload = kwargs["json"]
        if method == "PATCH":
            rid = path.split("/")[-1]
            row = next(r for r in self.rows if r["id"] == rid)
            row["fields"].update(payload["fields"])
            return row
        row = {"id": "recNew", "fields": payload["records"][0]["fields"]}
        self.rows.append(row)
        return {"records": [row]}


def setup_run(tmp_path, monkeypatch, source, api):
    config_path = tmp_path / "air.json"
    config_path.write_text(json.dumps({"AIRTABLE_BASE_ID": "app", "AIRTABLE_TABLE_ID": "tbl"}))
    config = {
        "state_dir": str(tmp_path / "state"),
        "airtable_config_path": str(config_path),
        "source_file_id": "source",
        "sheet_name": "Keywords",
    }
    monkeypatch.setattr(runtime, "read_source", lambda c: (source, {"version": "1"}))
    monkeypatch.setattr(runtime, "Airtable", lambda c: api)
    return config


def test_apply_twice_writes_once_and_keeps_publication_status(tmp_path, monkeypatch):
    before = sample()
    after = {**before, "Notes": "Full untruncated note"}
    api = FakeAPI([record(before)])
    config = setup_run(tmp_path, monkeypatch, [after], api)
    assert runtime.run(config, False)["updated"] == 1
    assert not api.writes
    assert runtime.run(config, True)["updated"] == 1
    assert runtime.run(config, True)["updated"] == 0
    assert len(api.writes) == 1
    assert api.rows[0]["fields"]["Publication Status"] == "Published"


def test_new_record_created_once(tmp_path, monkeypatch):
    api = FakeAPI([])
    config = setup_run(tmp_path, monkeypatch, [sample()], api)
    assert runtime.run(config, True)["created"] == 1
    assert runtime.run(config, True)["created"] == 0
    assert len(api.writes) == 1


def test_ambiguous_create_never_retried(tmp_path, monkeypatch):
    api = FakeAPI([], ambiguous=True)
    config = setup_run(tmp_path, monkeypatch, [sample()], api)
    with pytest.raises(TimeoutError):
        runtime.run(config, True)
    with pytest.raises(RuntimeError, match="Unresolved write journal"):
        runtime.run(config, True)
    assert len(api.writes) == 1
