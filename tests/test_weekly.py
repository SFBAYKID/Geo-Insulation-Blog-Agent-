"""Weekly production share and post-publication Basecamp completion."""

import json
from unittest.mock import Mock

import pytest

from geo_blog import basecamp_complete, production_publish, site_preview, weekly
from geo_blog.settings import Settings
from geo_blog.store import Store

PROD = "C_GEO_PRODUCTION_TEST"


def ready_draft(tmp_path, store, draft_id="wk1", status="ready"):
    payload = {
        "front_matter": {"title": "Weekly title", "description": "Summary"},
        "report": {"word_count": 1500},
        "topic": {"keyword": "k"},
        "preview_commit": "head",
        "preview_url": "https://x.vercel.app/blog/weekly",
        "pr_url": "https://github.com/GEO_WEBSITE_NOT_CONFIGURED/pull/50",
        "media_status": "ready",
        "media_provenance": {"sha256": "abc"},
    }
    store.reserve(draft_id, "daily:" + draft_id, "topic-" + draft_id)
    store.save(draft_id, payload)
    if status != "ready":
        with store.db() as db:
            db.execute("UPDATE drafts SET status=? WHERE id=?", (status, draft_id))
    (tmp_path / draft_id / "website").mkdir(parents=True)
    return payload


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        production_delivery_enabled=True,
        slack_production_channel_id="C_GEO_PRODUCTION_TEST",
        slack_approver_ids="U_GEO_APPROVER_ONE,U_GEO_APPROVER_TWO",
    )
    store = Store(tmp_path)
    client = Mock()
    client.chat_postMessage.side_effect = [{"ts": "parent"}, {"ts": "card"}]
    monkeypatch.setattr("geo_blog.slack_guard.WebClient", lambda token: client)
    commands = []

    def command(args, cwd):
        commands.append(args)
        return "head" if args[:2] == ["git", "rev-parse"] else ""

    monkeypatch.setattr(site_preview, "command", command)
    monkeypatch.setattr(site_preview, "require_ci_quality", Mock())
    pr = {"head": {"sha": "head"}, "state": "open", "draft": True}

    def api(checkout, endpoint, *args):
        return {"object": {"sha": "main-sha"}} if endpoint.endswith("/main") else pr

    monkeypatch.setattr(production_publish, "api", api)
    return settings, store, client, commands


def test_share_posts_parent_and_card_then_arms_exact_commit(tmp_path, env):
    settings, store, client, commands = env
    ready_draft(tmp_path, store)
    (tmp_path / "production-review.json").write_text(
        json.dumps({"state": "published", "draft_id": "old"})
    )
    (tmp_path / "weekly-next.json").write_text(json.dumps({"draft_id": "wk1"}))
    assert weekly.run_weekly(settings, store) == "wk1"
    parent, card = client.chat_postMessage.call_args_list
    assert parent.kwargs["channel"] == PROD and "<@U_GEO_APPROVER_ONE>" in parent.kwargs["text"]
    assert card.kwargs["thread_ts"] == "parent"
    buttons = [b for b in card.kwargs["blocks"] if b["type"] == "actions"][0]["elements"]
    assert {b["action_id"] for b in buttons} == {"geo_production_approve", "geo_production_reject"}
    assert all(b["value"] == "head" for b in buttons)
    review = json.loads((tmp_path / "production-review.json").read_text())
    plan = json.loads((tmp_path / "publication-plan.json").read_text())
    assert review["state"] == "pending" and review["message_ts"] == "card"
    assert plan == {
        "armed": True,
        "draft_id": "wk1",
        "head_sha": "head",
        "base_sha": "main-sha",
        "pr_number": 50,
    }
    assert ["gh", "pr", "ready", "50"] in commands
    assert list((tmp_path / "old").glob("production-review-final-*.json"))
    assert not (tmp_path / "weekly-next.json").exists()


@pytest.mark.parametrize("state", ["pending", "queued", "publication_needs_inspection"])
def test_open_review_blocks_new_week(tmp_path, env, state):
    settings, store, client, _ = env
    ready_draft(tmp_path, store)
    (tmp_path / "production-review.json").write_text(json.dumps({"state": state}))
    with pytest.raises(ValueError, match="still " + state):
        weekly.run_weekly(settings, store)
    client.chat_postMessage.assert_not_called()


def test_rejected_staged_draft_is_replaced_by_fresh_draft(tmp_path, env, monkeypatch):
    settings, store, client, _ = env
    ready_draft(tmp_path, store, "old", status="rejected")
    (tmp_path / "weekly-next.json").write_text(json.dumps({"draft_id": "old"}))

    def fresh(s, st, send):
        assert send is False
        ready_draft(tmp_path, st, "fresh")
        return "fresh"

    monkeypatch.setattr("geo_blog.cli.run_daily", fresh)
    assert weekly.run_weekly(settings, store) == "fresh"


def test_unresolved_share_is_never_resent(tmp_path, env):
    settings, store, client, _ = env
    ready_draft(tmp_path, store)
    (tmp_path / "wk1" / "weekly-share.json").write_text(json.dumps({"state": "sending"}))
    with pytest.raises(ValueError, match="unresolved"):
        weekly.share(settings, store, "wk1")
    client.chat_postMessage.assert_not_called()


def test_changed_checkout_is_not_shared(tmp_path, env, monkeypatch):
    settings, store, client, _ = env
    ready_draft(tmp_path, store)
    monkeypatch.setattr(site_preview, "command", lambda args, cwd: "other")
    with pytest.raises(ValueError, match="changed"):
        weekly.share(settings, store, "wk1")
    client.chat_postMessage.assert_not_called()


def test_stage_test_holds_basecamp_and_stages_draft(tmp_path, env, monkeypatch):
    settings, store, _, _ = env
    seen = {}

    def fake(s, st, send):
        seen.update(send=send, basecamp=s.basecamp_draft_ready_enabled)
        return "next"

    monkeypatch.setattr("geo_blog.cli.run_daily", fake)
    assert (
        weekly.stage_test(settings.model_copy(update={"basecamp_draft_ready_enabled": True}), store)
        == "next"
    )
    assert seen == {"send": True, "basecamp": False}
    assert json.loads((tmp_path / "weekly-next.json").read_text())["draft_id"] == "next"


class FakeBasecamp:
    def __init__(self, completed=False, comments=()):
        self.completed = completed
        self.comments_list = list(comments)
        self.posts = []
        self.bucket = "buckets/1/"
        self.root = "https://3.basecampapi.com/1/"
        self.headers = {}
        self.http = Mock()
        self.http.post.side_effect = self._complete

    def _complete(self, url, headers):
        self.completed = True
        return Mock(status_code=204)

    def __call__(self, settings):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def task(self, task_id):
        return {"completed": self.completed}

    def comments(self, task_id):
        return self.comments_list

    def request(self, method, path, json):
        self.posts.append(json["content"])


URL = "https://geo-insulation.com/blog/weekly"
DRAFT = {"topic": {"basecamp_task_id": 7}}


def test_live_url_is_commented_once_and_task_completed(monkeypatch):
    fake = FakeBasecamp()
    monkeypatch.setattr(basecamp_complete, "Basecamp", fake)
    assert basecamp_complete.record_live(Settings(_env_file=None), DRAFT, URL) == "complete"
    assert len(fake.posts) == 1 and URL in fake.posts[0] and fake.completed


def test_restart_does_not_repost_or_recomplete(monkeypatch):
    fake = FakeBasecamp(completed=True, comments=[{"content": URL}])
    monkeypatch.setattr(basecamp_complete, "Basecamp", fake)
    assert basecamp_complete.record_live(Settings(_env_file=None), DRAFT, URL) == "complete"
    assert not fake.posts and not fake.http.post.called


def test_non_live_url_and_missing_task_are_refused_or_skipped():
    s = Settings(_env_file=None)
    assert basecamp_complete.record_live(s, {"topic": {}}, URL) == "skipped"
    with pytest.raises(ValueError):
        basecamp_complete.record_live(s, DRAFT, "https://x.vercel.app/blog/weekly")


def test_basecamp_failure_does_not_undo_publication(tmp_path, monkeypatch):
    s = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        production_delivery_enabled=True,
        slack_production_channel_id="C_GEO_PRODUCTION_TEST",
        slack_approver_ids="U_GEO_APPROVER_ONE,U_GEO_APPROVER_TWO",
    )
    (tmp_path / "production-review.json").write_text(json.dumps({"state": "published"}))
    monkeypatch.setattr(
        basecamp_complete, "record_live", Mock(side_effect=ValueError("Basecamp down"))
    )
    alert = Mock()
    monkeypatch.setattr(weekly, "private_alert", alert)
    monkeypatch.setattr(production_publish.time, "sleep", Mock())
    production_publish.record_basecamp(s, DRAFT, URL)
    assert URL in alert.call_args.args[1]
    review = json.loads((tmp_path / "production-review.json").read_text())
    assert review == {"state": "published", "basecamp_state": "needs_inspection"}


def test_blog_without_approved_photo_never_reaches_client_reviewer(tmp_path, env):
    settings, store, client, _ = env
    ready_draft(tmp_path, store)
    row = store.required("wk1")
    payload = json.loads(row["payload"])
    payload.update(media_status="awaiting_approved_match", media_provenance=None)
    with store.db() as db:
        db.execute("UPDATE drafts SET payload=? WHERE id='wk1'", (json.dumps(payload),))
    with pytest.raises(ValueError, match="photo"):
        weekly.share(settings, store, "wk1")
    client.chat_postMessage.assert_not_called()


def test_failure_is_reported_in_test_channel_never_production(tmp_path, env):
    settings, store, client, _ = env
    client.chat_postMessage.side_effect = None
    client.chat_postMessage.return_value = {"ts": "n"}
    (tmp_path / "production-review.json").write_text(json.dumps({"state": "pending"}))
    with pytest.raises(ValueError):
        weekly.run_weekly_notifying(settings, store)
    (call,) = client.chat_postMessage.call_args_list
    assert call.kwargs["channel"] == settings.slack_test_channel_id != PROD
    assert "<@U01DPJVURHU>" in call.kwargs["text"] and "still pending" in call.kwargs["text"]


def test_empty_week_is_reported_instead_of_silent(tmp_path, env, monkeypatch):
    settings, store, client, _ = env
    monkeypatch.setattr("geo_blog.cli.run_daily", lambda s, st, send: None)
    with pytest.raises(ValueError, match="No finished blog"):
        weekly.run_weekly(settings, store)
    client.chat_postMessage.assert_not_called()
