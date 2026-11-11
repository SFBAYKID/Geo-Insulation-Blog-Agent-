"""Approved-topic ownership, intent holds, and failure recovery without live providers."""

import copy
from unittest.mock import Mock

import httpx
import pytest

from geo_blog import research_api, research_lifecycle, research_queue
from geo_blog.settings import Settings
from geo_blog.store import Store


def config(tmp_path):
    return Settings(
        _env_file=None,
        storage_dir=tmp_path,
        blog_queue_source="research",
        airtable_token="test-only",
        airtable_base_id=research_api.BASE,
        airtable_table=research_api.POSTS,
        airtable_write_enabled=True,
        openai_api_key="test-only",
    )


def row(
    identity="recA",
    status="Proposed",
    priority=100,
    created="2026-10-01T00:00:00Z",
    keyword="attic comfort",
):
    return {
        "id": identity,
        "createdTime": created,
        "fields": {
            "Status": status,
            "Primary keyword": keyword,
            "Supporting keywords": "home comfort\nattic airflow",
            "Priority": priority,
            "Notes / rationale": "Measurements belong here.",
            "Target size (words)": "600",
            "Planned URL": "/blog/" + identity.lower(),
        },
    }


class FakeAPI:
    def __init__(self, rows, coverage=None):
        self.records = {r["id"]: copy.deepcopy(r) for r in rows}
        self.coverage = coverage or []
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def rows(self, table=research_api.POSTS):
        return copy.deepcopy(
            list(self.records.values()) if table == research_api.POSTS else self.coverage
        )

    def get(self, record):
        return copy.deepcopy(self.records[record])

    def patch(self, record, fields):
        assert set(fields) <= research_api.WRITABLE
        self.writes.append((record, copy.deepcopy(fields)))
        self.records[record]["fields"].update(fields)
        return self.get(record)


def wire(monkeypatch, api):
    monkeypatch.setattr(research_queue, "ResearchAPI", lambda settings: api)
    monkeypatch.setattr(research_lifecycle, "ResearchAPI", lambda settings: api)
    monkeypatch.setattr(research_queue, "basecamp_coverage", lambda settings: [])
    monkeypatch.setattr(research_queue, "live_paths", lambda: set())
    fallback = Mock(return_value=None)
    monkeypatch.setattr("geo_blog.basecamp_queue.select_basecamp_topic", fallback)
    return fallback


def test_selection_exact_status_priority_age_and_secondary(tmp_path, monkeypatch):
    api = FakeAPI(
        [
            row("recHeld", "Held", 1, keyword="held separate topic"),
            row("recBlank", "", 1),
            row("recLate", priority=2, created="2026-10-02T00:00:00Z"),
            row("recFirst", priority=2),
            row("recDefault", priority=None),
        ]
    )
    fallback = wire(monkeypatch, api)
    topic = research_queue.select_research(config(tmp_path), set())
    assert topic["id"] == "recFirst"
    assert topic["secondary_keyword"] == "home comfort"
    assert topic["supporting_keywords"] == ["home comfort", "attic airflow"]
    assert topic["notes"] == "Measurements belong here."
    assert "target_words" not in topic
    assert api.writes == []
    fallback.assert_not_called()


def test_duplicate_is_held_not_rewritten_or_rejected(tmp_path, monkeypatch):
    api = FakeAPI(
        [row()],
        [
            {
                "fields": {
                    "Primary keyword": "Attic comfort",
                    "Published URL": "https://geo-insulation.com/attic/",
                }
            }
        ],
    )
    wire(monkeypatch, api)
    original = api.get("recA")["fields"]
    assert research_queue.select_research(config(tmp_path), set(), hold_duplicates=True) is None
    assert api.writes[0][1]["Status"] == "Held"
    assert "review search intent" in api.writes[0][1]["Hold reason"]
    assert {
        k: v for k, v in api.get("recA")["fields"].items() if k not in {"Status", "Hold reason"}
    } == {k: v for k, v in original.items() if k != "Status"}


def test_readonly_inspection_does_not_hold_duplicate(tmp_path, monkeypatch):
    api = FakeAPI([row()])
    wire(monkeypatch, api)
    monkeypatch.setattr(research_queue, "live_paths", lambda: {"/blog/reca"})
    assert research_queue.select_research(config(tmp_path), set()) is None
    assert not api.writes


def test_shared_words_do_not_prove_overlap():
    assert (
        research_queue.overlap(
            {"Primary keyword": "attic comfort"}, [{"Primary keyword": "attic ventilation"}], set()
        )
        == ""
    )


def test_empty_falls_back_but_api_error_does_not(tmp_path, monkeypatch):
    api = FakeAPI([])
    fallback = wire(monkeypatch, api)
    fallback.return_value = {"keyword": "new topic", "planned_url": "/blog/new"}
    assert research_queue.select_research(config(tmp_path), set()) == fallback.return_value
    fallback.reset_mock()
    api.rows = Mock(side_effect=ValueError("unavailable"))
    with pytest.raises(ValueError):
        research_queue.select_research(config(tmp_path), set())
    fallback.assert_not_called()


def test_basecamp_cannot_bypass_held_topic(tmp_path, monkeypatch):
    api = FakeAPI([row(status="Held")])
    fallback = wire(monkeypatch, api)
    fallback.return_value = {"keyword": "attic comfort"}
    with pytest.raises(ValueError, match="overlaps"):
        research_queue.select_research(config(tmp_path), set())
    assert not api.writes


def test_claim_rereads_and_lifecycle_preserves_source(tmp_path, monkeypatch):
    api = FakeAPI([row()])
    wire(monkeypatch, api)
    settings = config(tmp_path)
    topic = research_queue.select_research(settings, set())
    (tmp_path / "draft").mkdir()
    research_lifecycle.claim(settings, topic, "draft")
    research_lifecycle.transition(settings, topic, "Drafting")
    research_lifecycle.transition(settings, topic, "Review")
    research_lifecycle.transition(
        settings, topic, "Published", url="https://geo-insulation.com/blog/reca/"
    )
    assert [fields["Status"] for _, fields in api.writes] == [
        "Claimed",
        "Drafting",
        "Review",
        "Published",
    ]
    assert api.get("recA")["fields"]["Notes / rationale"] == "Measurements belong here."


def test_changed_row_cannot_be_claimed(tmp_path, monkeypatch):
    api = FakeAPI([row()])
    wire(monkeypatch, api)
    settings = config(tmp_path)
    topic = research_queue.select_research(settings, set())
    api.records["recA"]["fields"]["Status"] = "Held"
    (tmp_path / "draft").mkdir()
    with pytest.raises(ValueError, match="changed"):
        research_lifecycle.claim(settings, topic, "draft")
    assert not api.writes


def test_failed_draft_returns_to_proposed_and_releases_topic(tmp_path, monkeypatch):
    from geo_blog.cli import run_daily

    api = FakeAPI([row()])
    wire(monkeypatch, api)
    writer = Mock()
    writer.draft.side_effect = ValueError("editor failed")
    monkeypatch.setattr("geo_blog.content.Writer", lambda settings: writer)
    store = Store(tmp_path)
    with pytest.raises(ValueError, match="editor failed"):
        run_daily(config(tmp_path), store)
    assert [fields["Status"] for _, fields in api.writes] == ["Claimed", "Drafting", "Proposed"]
    assert "recA" not in store.used_topics()


def test_unowned_or_human_held_claim_is_not_reset(tmp_path, monkeypatch):
    api = FakeAPI([row(status="Held")])
    wire(monkeypatch, api)
    topic = {"research_record_id": "recA", "research_claimed_at": "2026-10-01T00:00:00Z"}
    with pytest.raises(ValueError, match="ownership"):
        research_lifecycle.transition(config(tmp_path), topic, "Proposed")
    assert not api.writes


def test_api_rejects_research_field_and_coverage_writes(tmp_path):
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req)))
    with research_api.ResearchAPI(config(tmp_path), client) as api:
        with pytest.raises(ValueError):
            api.patch("recA", {"Status": "Drafting", "Primary keyword": "changed"})
        with pytest.raises(ValueError):
            api.request("PATCH", research_api.COVERAGE, "recA")
    assert not calls


def test_impossible_primary_is_held_and_next_topic_selected(tmp_path, monkeypatch):
    api = FakeAPI([row("recLong", priority=1, keyword="x" * 56), row("recNext", priority=2)])
    wire(monkeypatch, api)
    topic = research_queue.select_research(config(tmp_path), set(), hold_duplicates=True)
    assert topic["id"] == "recNext"
    assert api.writes[0][0] == "recLong"
    assert api.writes[0][1]["Status"] == "Held"
    assert "title" in api.writes[0][1]["Hold reason"]


def test_impossible_primary_is_skipped_read_only_during_inspection(tmp_path, monkeypatch):
    api = FakeAPI([row("recLong", priority=1, keyword="x" * 56), row("recNext", priority=2)])
    wire(monkeypatch, api)
    assert research_queue.select_research(config(tmp_path), set())["id"] == "recNext"
    assert not api.writes


def quality_failure(monkeypatch, tmp_path, store, api, exc=None):
    """Run one draft that exhausts the writer, leaving the artifacts a real run leaves."""
    from geo_blog.cli import run_daily
    from geo_blog.content import WRITER_ATTEMPTS
    from geo_blog.writer_feedback import DraftQualityError

    def draft(topic, day, folder):
        (folder / f"writer-{WRITER_ATTEMPTS - 1}.json").write_text("{}")
        raise exc or DraftQualityError(topic["keyword"], WRITER_ATTEMPTS, ["h1 (Exactly one H1)"])

    monkeypatch.setattr("geo_blog.content.Writer", lambda settings: Mock(draft=draft))
    # A distinct run key per simulated week; the daily key would dedupe same-day runs.
    with pytest.raises(Exception) as caught:
        run_daily(config(tmp_path), store, request_key=f"week-{len(api.writes)}")
    return caught.value


def test_second_quality_failure_holds_topic_and_flags_alert(tmp_path, monkeypatch):
    api = FakeAPI([row()])
    wire(monkeypatch, api)
    store = Store(tmp_path)
    first = quality_failure(monkeypatch, tmp_path, store, api)
    assert api.records["recA"]["fields"]["Status"] == "Proposed"
    assert not hasattr(first, "topic_held")
    second = quality_failure(monkeypatch, tmp_path, store, api)
    fields = api.records["recA"]["fields"]
    assert fields["Status"] == "Held"
    assert "2 separate runs" in fields["Hold reason"]
    assert "h1 (Exactly one H1)" in fields["Hold reason"]
    assert "now Held" in second.topic_held
    # Locally still requeued, so Chase can set it back to Proposed after editing.
    assert "recA" not in store.used_topics()


def test_outages_never_hold_a_topic(tmp_path, monkeypatch):
    api = FakeAPI([row()])
    wire(monkeypatch, api)
    store = Store(tmp_path)
    for _ in range(3):
        quality_failure(monkeypatch, tmp_path, store, api, exc=ValueError("OpenAI timeout"))
        assert api.records["recA"]["fields"]["Status"] == "Proposed"


def test_edited_keywords_reset_the_failure_count(tmp_path, monkeypatch):
    api = FakeAPI([row()])
    wire(monkeypatch, api)
    store = Store(tmp_path)
    quality_failure(monkeypatch, tmp_path, store, api)
    api.records["recA"]["fields"]["Supporting keywords"] = "attic airflow"
    quality_failure(monkeypatch, tmp_path, store, api)
    assert api.records["recA"]["fields"]["Status"] == "Proposed"


def test_failures_from_before_the_fix_do_not_count(tmp_path, monkeypatch):
    """A Nov 11 style failure folder (no marker) must not count as a first strike."""
    api = FakeAPI([row()])
    wire(monkeypatch, api)
    store = Store(tmp_path)
    old = tmp_path / "olddraft"
    old.mkdir()
    (old / "writer-6.json").write_text("{}")
    (old / "topic.json").write_text(
        '{"keyword": "attic comfort", "secondary_keyword": "home comfort"}'
    )
    with store.db() as db:
        db.execute(
            "INSERT INTO drafts(id,run_key,topic_key,status,error) VALUES('olddraft','daily:x','recA','failed_requeued','ValueError')"
        )
    quality_failure(monkeypatch, tmp_path, store, api)
    assert api.records["recA"]["fields"]["Status"] == "Proposed"


def test_held_topic_reproposed_unchanged_is_held_again_without_drafting(tmp_path, monkeypatch):
    api = FakeAPI([row(), row("recNext", priority=200, keyword="attic airflow basics")])
    wire(monkeypatch, api)
    store = Store(tmp_path)
    quality_failure(monkeypatch, tmp_path, store, api)
    quality_failure(monkeypatch, tmp_path, store, api)
    assert api.records["recA"]["fields"]["Status"] == "Held"
    # The research agent's sync (or a person) sets it back to Proposed with no edits.
    api.records["recA"]["fields"]["Status"] = "Proposed"
    topic = research_queue.select_research(config(tmp_path), set(), hold_duplicates=True)
    assert topic["id"] == "recNext"
    assert api.records["recA"]["fields"]["Status"] == "Held"
    assert "re-proposing it unchanged" in api.records["recA"]["fields"]["Hold reason"]
    assert "h1 (Exactly one H1)" in api.records["recA"]["fields"]["Hold reason"]


def test_edited_brief_releases_a_held_topic(tmp_path, monkeypatch):
    api = FakeAPI([row()])
    wire(monkeypatch, api)
    store = Store(tmp_path)
    quality_failure(monkeypatch, tmp_path, store, api)
    quality_failure(monkeypatch, tmp_path, store, api)
    fields = api.records["recA"]["fields"]
    fields.update({"Status": "Proposed", "Angle": "Focus on measured attic airflow."})
    topic = research_queue.select_research(config(tmp_path), set(), hold_duplicates=True)
    assert topic["id"] == "recA"


def test_impossible_basecamp_task_is_skipped_and_reported(tmp_path, monkeypatch):
    from geo_blog import cli

    settings = config(tmp_path).model_copy(update={"blog_queue_source": "basecamp"})
    bad = {"id": "basecamp:1", "keyword": "x" * 60}
    good = {"id": "basecamp:2", "keyword": "attic comfort"}
    monkeypatch.setattr(
        cli, "select_topic", lambda s, used: bad if "basecamp:1" not in used else good
    )
    alerts = []
    monkeypatch.setattr("geo_blog.weekly.private_alert", lambda s, text: alerts.append(text))
    monkeypatch.setattr(cli.Store, "reserve", lambda *args: False)  # stop before drafting
    assert cli.run_daily(settings, Store(tmp_path)) is None
    assert "basecamp:1" in alerts[0] and "title" in alerts[0]


def test_direct_request_with_impossible_keyword_is_refused(tmp_path):
    from geo_blog import cli

    settings = config(tmp_path).model_copy(update={"blog_queue_source": "basecamp"})
    with pytest.raises(ValueError, match="website limits"):
        cli.run_daily(settings, Store(tmp_path), topic={"id": "req", "keyword": "x" * 60})
