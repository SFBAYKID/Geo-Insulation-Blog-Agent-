import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from geo_blog import nightly
from geo_blog.settings import Settings
from geo_blog.store import Store


def setup(tmp_path, monkeypatch):
    s = Settings(
        _env_file=None,
        website_base_commit="test-base-commit",
        storage_dir=tmp_path,
        openai_api_key="test",
        airtable_token="test",
        slack_bot_token="test",
        slack_approver_ids="owner",
    )
    store = Store(tmp_path)
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    client.chat_postMessage.return_value = {"ts": "123.456"}
    monkeypatch.setattr(nightly, "safe_client", lambda settings: client)
    monkeypatch.setattr(
        nightly,
        "select_topic",
        lambda *args: {
            "id": "topic",
            "keyword": "example",
            "secondary_keyword": "support",
        },
    )
    return s, store, client


def test_google_failure_has_no_writer_preview_or_review(tmp_path, monkeypatch):
    s, store, client = setup(tmp_path, monkeypatch)

    def blocked(*args, **kwargs):
        raise RuntimeError("challenge")

    monkeypatch.setattr(nightly.subprocess, "run", blocked)
    writer = Mock()
    monkeypatch.setattr(nightly, "Writer", writer)
    with pytest.raises(RuntimeError):
        nightly._run(s, store)
    writer.assert_not_called()
    assert "No draft is ready to approve" in client.chat_postMessage.call_args.kwargs["text"]
    assert "thread_ts" in client.chat_postMessage.call_args.kwargs
    client.files_upload_v2.assert_not_called()
    # Same day never triggers duplicate generation or duplicate notices.
    count = client.chat_postMessage.call_count
    nightly._run(s, store)
    assert client.chat_postMessage.call_count == count


def test_complete_order_requires_preview_before_delivery(tmp_path, monkeypatch):
    s, store, client = setup(tmp_path, monkeypatch)
    events = []

    def browser(args, **kwargs):
        folder = (
            tmp_path
            / ("nightly-" + datetime.now(nightly.ZoneInfo(s.timezone)).date().isoformat())
            / "competitors"
        )
        (folder / "google-search.json").write_text(
            json.dumps(
                {
                    "source": "google_browser",
                    "query": "example",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "search_url": "https://www.google.com/search?q=example",
                    "results": [
                        {
                            "position": i,
                            "title": str(i),
                            "url": f"https://site{i}.test/",
                            "kind": "organic_page",
                        }
                        for i in range(1, 4)
                    ],
                }
            )
        )
        events.append("browser")

    monkeypatch.setattr(nightly.subprocess, "run", browser)
    monkeypatch.setattr(nightly, "update_thread", lambda *args: events.append("competitors"))
    writer = Mock()
    writer.draft.side_effect = lambda *args: (
        events.append("write") or {"topic": {}, "markdown": "article"}
    )
    monkeypatch.setattr(nightly, "Writer", lambda *args: writer)
    monkeypatch.setattr(
        nightly, "complete_visuals", lambda w, d, f, day: events.append("visuals") or d
    )

    def preview(settings, st, id):
        assert st.get(id)["status"] == "ready"
        events.append("preview")
        st.attach_preview(id, "https://example.vercel.app/blog/test/", "Checked")
        return {"preview_url": "https://example.vercel.app/blog/test/"}

    monkeypatch.setattr(nightly, "deploy_preview", preview)

    def delivery(settings, st, id, cl):
        assert json.loads(st.get(id)["payload"])["preview_url"]
        events.append("deliver")
        assert st.claim_delivery(id, s.slack_channel_id)
        st.delivered(id, "124.456")

    monkeypatch.setattr(nightly, "deliver", delivery)
    id = nightly._run(s, store)
    assert events == [
        "browser",
        "competitors",
        "write",
        "visuals",
        "preview",
        "deliver",
    ]
    assert store.get(id)["status"] == "pending"
    # Successful runs announce the topic once; no running commentary.
    assert client.chat_postMessage.call_count == 1
    assert "website preview" in client.chat_postMessage.call_args.kwargs["text"]


def test_prepared_draft_cannot_replace_another_topic(tmp_path, monkeypatch):
    s, store, client = setup(tmp_path, monkeypatch)
    id = "nightly-" + datetime.now(nightly.ZoneInfo(s.timezone)).date().isoformat()
    folder = tmp_path / id
    folder.mkdir()
    (folder / "prepared-payload.json").write_text(json.dumps({"topic": {"id": "wrong"}}))
    evidence = folder / "competitors"
    evidence.mkdir()
    (evidence / "google-search.json").write_text(
        json.dumps(
            {
                "source": "google_browser",
                "query": "example",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "search_url": "https://www.google.com/search?q=example",
                "results": [
                    {
                        "position": i,
                        "title": str(i),
                        "url": f"https://site{i}.test/",
                        "kind": "organic_page",
                    }
                    for i in range(1, 4)
                ],
            }
        )
    )
    monkeypatch.setattr(nightly, "update_thread", Mock())
    monkeypatch.setattr(nightly, "Writer", Mock())
    deploy = Mock()
    monkeypatch.setattr(nightly, "deploy_preview", deploy)
    with pytest.raises(ValueError, match="another topic"):
        nightly._run(s, store)
    deploy.assert_not_called()


def test_model_json_does_not_accept_ambiguous_or_malformed_output():
    from types import SimpleNamespace

    from geo_blog.content import response_json

    def message(t):
        return SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=t)]
        )

    assert response_json(message('Reasoning.\n```json\n{"candidates":[]}\n```')) == {
        "candidates": []
    }
    with pytest.raises(ValueError):
        response_json(message("not JSON"))
    with pytest.raises(ValueError):
        response_json(message("```json\n{}\n```\n```json\n{}\n```"))


def test_official_source_allowlist_rejects_lookalikes():
    from geo_blog.content import primary_urls

    allowed = {"https://www.energy.gov/repairs", "https://www.energystar.gov/example"}
    assert (
        primary_urls(
            allowed | {"https://energy.gov.bad.test/docs", "https://sqmagazine.co.uk/openai/"}
        )
        == allowed
    )


def test_nightly_delivery_cannot_bypass_research_gate(tmp_path):
    from geo_blog.slack_app import deliver

    s = Settings(_env_file=None, slack_bot_token="test", slack_approver_ids="owner")
    st = Store(tmp_path)
    st.reserve("night", "day", "topic")
    st.save(
        "night",
        {"nightly_run": True, "preview_url": "https://example.vercel.app/blog/"},
    )
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    with pytest.raises(ValueError, match="verified competitors"):
        deliver(s, st, "night", client)
    client.files_upload_v2.assert_not_called()
    assert st.get("night")["status"] == "ready"


def test_nightly_review_is_one_final_card_without_async_file(tmp_path):
    from geo_blog.slack_app import deliver

    s = Settings(_env_file=None, slack_bot_token="test", slack_approver_ids="owner")
    st = Store(tmp_path)
    st.reserve("night", "day", "topic")
    st.save(
        "night",
        {
            "nightly_run": True,
            "google_verified": True,
            "preview_url": "https://example.vercel.app/blog/",
            "front_matter": {"title": "Title", "description": "Summary"},
            "markdown": "Full article",
            "report": {"word_count": 1400},
        },
    )
    st.set_thread("night", s.slack_channel_id, "100.1")
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    client.chat_postMessage.return_value = {"ts": "101.1"}
    deliver(s, st, "night", client)
    client.files_upload_v2.assert_not_called()
    assert client.chat_postMessage.call_count == 1
    sent = client.chat_postMessage.call_args.kwargs
    assert sent["thread_ts"] == "100.1"
    assert sent["blocks"][-1]["type"] == "actions"
    assert sent["blocks"][-1]["elements"][0]["action_id"] == "blog_approve"
    assert "https://example.vercel.app/blog/" in json.dumps(sent["blocks"])
    assert st.get("night")["status"] == "pending"


def test_schedule_skips_consumed_day_and_uses_local_time():
    from zoneinfo import ZoneInfo

    from geo_blog.slack_app import next_draft_schedule

    now = datetime(2026, 9, 15, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert (
        next_draft_schedule("nightly-2026-09-15", now=now)
        == "I’m scheduled to start your next blog on Wednesday, September 16 at 9 PM Pacific."
    )
    later = datetime(2026, 9, 17, 22, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert "Friday, September 18 at 9 PM Pacific" in next_draft_schedule(
        "nightly-2026-09-15", now=later
    )


def test_schedule_is_inside_card_before_approval():
    from geo_blog.slack_app import review_blocks

    draft = {
        "nightly_run": True,
        "front_matter": {"title": "Title", "description": "Summary"},
        "report": {"word_count": 1400},
    }
    blocks = review_blocks(
        draft,
        "nightly-2026-09-15",
        settings=Settings(_env_file=None).model_copy(update={"daily_enabled": True}),
    )
    assert "scheduled to start your next blog" in blocks[-2]["elements"][0]["text"]
    assert blocks[-1]["type"] == "actions"


@pytest.mark.parametrize("status", ["sending", "pending", "approved", "published"])
def test_explicit_old_retry_never_repeats_delivery(tmp_path, monkeypatch, status):
    s, store, client = setup(tmp_path, monkeypatch)
    old = "nightly-2026-01-01"
    store.reserve(old, "daily:2026-01-01", "topic")
    with store.db() as db:
        db.execute("UPDATE drafts SET status=? WHERE id=?", (status, old))
    select = Mock(side_effect=AssertionError("Never select another topic"))
    monkeypatch.setattr(nightly, "select_topic", select)
    assert nightly._run(s, store, retry=True, draft_id=old) == old
    select.assert_not_called()
    client.auth_test.assert_not_called()
    client.chat_postMessage.assert_not_called()


def test_explicit_retry_needs_saved_reservation_and_retry_flag(tmp_path, monkeypatch):
    s, store, client = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        nightly._run(s, store, draft_id="nightly-2026-01-01")
    with pytest.raises(ValueError):
        nightly._run(s, store, retry=True, draft_id="nightly-2026-01-01")
    with pytest.raises(ValueError):
        nightly._run(s, store, retry=True, draft_id="../../other")
    assert not store.used_topics()
    client.chat_postMessage.assert_not_called()


def test_retry_after_midnight_uses_original_topic_thread_and_day(tmp_path, monkeypatch):
    s, store, client = setup(tmp_path, monkeypatch)
    old = "nightly-2026-01-01"
    store.reserve(old, "daily:2026-01-01", "topic")
    store.set_thread(old, s.slack_channel_id, "200.1")
    store.fail(old, "old error")
    folder = tmp_path / old
    folder.mkdir()
    (folder / "topic.json").write_text(
        json.dumps(
            {
                "id": "topic",
                "keyword": "example",
                "secondary_keyword": "MEASURED SUPPORTING KEYWORDS",
                "supporting_keywords_brief": "MEASURED SUPPORTING KEYWORDS\nsupport phrase — 90/mo",
            }
        )
    )
    monkeypatch.setattr(
        nightly,
        "select_topic",
        Mock(side_effect=AssertionError("Never select another topic")),
    )
    # Stop at the research boundary after capturing the corrected saved brief.
    monkeypatch.setattr(nightly.subprocess, "run", Mock(side_effect=RuntimeError("stop here")))
    with pytest.raises(RuntimeError):
        nightly._run(s, store, retry=True, draft_id=old)
    assert json.loads((folder / "topic.json").read_text())["secondary_keyword"] == "support phrase"
    assert store.get(old)["thread_ts"] == "200.1"
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM drafts").fetchone()[0] == 1
    assert all(c.kwargs.get("thread_ts") == "200.1" for c in client.chat_postMessage.call_args_list)


def test_public_nightly_entry_is_disabled(tmp_path):
    with pytest.raises(RuntimeError, match="Unattended"):
        nightly.run_nightly(Settings(_env_file=None), Store(tmp_path))
