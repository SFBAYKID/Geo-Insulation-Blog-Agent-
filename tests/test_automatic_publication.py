"""Automatic publication, Central delivery hours, and authenticated Undo regressions."""

import json
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from geo_blog import automatic_publication as auto
from geo_blog import production_publish as pub
from geo_blog import publication_delivery as delivery
from geo_blog import publication_undo as undo
from geo_blog.settings import Settings
from geo_blog.undo_registry import verify_article_addition


def settings(tmp_path):
    return Settings(
        _env_file=None,
        storage_dir=tmp_path,
        production_auto_publish_enabled=True,
        production_delivery_enabled=True,
        publishing_enabled=True,
        website_preview_enabled=True,
        website_repository=pub.REPOSITORY,
        slack_production_channel_id="C_PROD",
        slack_team_id="T_GEO",
        slack_approver_ids="U_OWNER",
    )


@pytest.mark.parametrize(
    "iso,expected",
    [
        ("2026-11-10T08:59:00", False),
        ("2026-11-10T09:00:00", True),
        ("2026-11-10T16:59:00", True),
        ("2026-11-10T17:00:00", False),
        ("2026-11-14T11:00:00", False),
        ("2026-11-15T11:00:00", False),
        ("2026-07-08T11:00:00", True),
    ],
)
def test_central_business_hours(iso, expected):
    assert (
        delivery.business_hours(
            datetime.fromisoformat(iso).replace(tzinfo=ZoneInfo("America/Chicago"))
        )
        is expected
    )


def record(s, draft_id="first", existing=False):
    review = dict(
        draft_id=draft_id,
        preview_commit="head",
        production_commit="merged",
        live_url="https://geo-insulation.com/blog/example/",
    )
    if existing:
        review.update(message_ts="card", thread_ts="parent")
    delivery.save_publication(s, review, {"front_matter": {"title": "Example", "slug": "example"}})
    return delivery.publication_path(s, draft_id)


def test_overnight_notification_is_durable_then_sent_once(tmp_path, monkeypatch):
    s = settings(tmp_path)
    path = record(s)
    client = Mock()
    client.chat_postMessage.return_value = {"ts": "posted"}
    monkeypatch.setattr("geo_blog.slack_guard.WebClient", lambda token: client)
    monkeypatch.setattr(delivery, "business_hours", lambda: False)
    delivery.deliver_pending(s)
    client.chat_postMessage.assert_not_called()
    assert json.loads(path.read_text())["notification_state"] == "pending"
    monkeypatch.setattr(delivery, "business_hours", lambda: True)
    delivery.deliver_pending(s)
    delivery.deliver_pending(s)
    client.chat_postMessage.assert_called_once()
    buttons = client.chat_postMessage.call_args.kwargs["blocks"][1]["elements"]
    assert len(buttons) == 1 and buttons[0]["action_id"] == "geo_publication_undo"
    assert "approve" not in json.dumps(buttons).lower()


def test_uncertain_send_is_not_retried(tmp_path, monkeypatch):
    s = settings(tmp_path)
    path = record(s)
    client = Mock()
    client.chat_postMessage.side_effect = TimeoutError()
    monkeypatch.setattr("geo_blog.slack_guard.WebClient", lambda token: client)
    monkeypatch.setattr(delivery, "business_hours", lambda: True)
    monkeypatch.setattr("geo_blog.weekly.private_alert", Mock())
    delivery.deliver_pending(s)
    delivery.deliver_pending(s)
    assert json.loads(path.read_text())["notification_state"] == "needs_inspection"
    client.chat_postMessage.assert_called_once()


def test_current_card_updates_in_place_without_new_overnight_post(tmp_path, monkeypatch):
    s = settings(tmp_path)
    record(s, existing=True)
    client = Mock()
    monkeypatch.setattr("geo_blog.slack_guard.WebClient", lambda token: client)
    monkeypatch.setattr(delivery, "business_hours", lambda: False)
    delivery.deliver_pending(s)
    assert client.chat_update.call_count == 2
    client.chat_postMessage.assert_not_called()
    assert {c.kwargs["ts"] for c in client.chat_update.call_args_list} == {"card", "parent"}


def test_auto_policy_requires_matching_plan_and_enabled_setting(tmp_path):
    s = settings(tmp_path)
    auto.arm(s, "draft", "head", "base", 42)
    review = json.loads((tmp_path / "production-review.json").read_text())
    plan = json.loads((tmp_path / "publication-plan.json").read_text())
    assert review["state"] == "queued" and "reviewer" not in review
    pub.validate_approval(review, plan, s.approvers, automatic=True)
    with pytest.raises(ValueError):
        pub.validate_approval(review, plan, s.approvers)
    plan["head_sha"] = "changed"
    with pytest.raises(ValueError):
        pub.validate_approval(review, plan, s.approvers, automatic=True)


def undo_body():
    return dict(
        team={"id": "T_GEO"},
        channel={"id": "C_PROD"},
        user={"id": "U_OWNER"},
        message={"ts": "card"},
        actions=[{"action_id": "geo_publication_undo", "value": "first:merged"}],
    )


@pytest.mark.parametrize("field", ["team", "channel", "user", "message", "commit"])
def test_undo_rejects_wrong_identity_or_version(tmp_path, field):
    s = settings(tmp_path)
    path = record(s, existing=True)
    body = undo_body()
    if field == "commit":
        body["actions"][0]["value"] = "first:other"
    else:
        body[field] = {"ts": "wrong"} if field == "message" else {"id": "wrong"}
    assert not undo.handle_undo(s, body, Mock())
    assert json.loads(path.read_text())["state"] == "published"


def test_old_blog_undo_remains_addressable_and_duplicate_click_is_ignored(tmp_path):
    s = settings(tmp_path)
    path = record(s, existing=True)
    record(s, "newer")
    (tmp_path / "production-review.json").write_text(json.dumps({"draft_id": "newer"}))
    assert undo.handle_undo(s, undo_body(), Mock())
    assert not undo.handle_undo(s, undo_body(), Mock())
    assert json.loads(path.read_text())["state"] == "undo_queued"


def test_revert_proof_rejects_unrelated_registry_edits():
    before = 'export const posts = [\n{"slug":"old"}\n];'
    new = (
        "export const posts = [\n"
        + json.dumps({"slug": "mine", "agentManaged": True})
        + ","
        + before.split("[", 1)[1]
    )
    verify_article_addition(before, new, "mine")
    with pytest.raises(ValueError):
        verify_article_addition(before, new.replace("old", "changed"), "mine")
    with pytest.raises(ValueError):
        verify_article_addition(before, new, "another")


def test_interrupted_undo_never_replays_external_mutations(tmp_path, monkeypatch):
    s = settings(tmp_path)
    path = record(s, existing=True)
    data = json.loads(path.read_text())
    data["state"] = "undo_preparing"
    path.write_text(json.dumps(data))
    prepare = Mock()
    monkeypatch.setattr(undo, "prepare_revert", prepare)
    monkeypatch.setattr("geo_blog.weekly.private_alert", Mock())
    undo.run_pending(s)
    prepare.assert_not_called()
    assert json.loads(path.read_text())["state"] == "undo_needs_inspection"


def test_migration_replaces_current_card_and_queues_without_approval(tmp_path, monkeypatch):
    s = settings(tmp_path)
    review = dict(
        state="pending",
        draft_id="draft",
        preview_commit="head",
        message_ts="card",
        thread_ts="parent",
        comments=[],
    )
    (tmp_path / "production-review.json").write_text(json.dumps(review))
    (tmp_path / "publication-plan.json").write_text(
        json.dumps(dict(armed=True, draft_id="draft", head_sha="head"))
    )
    client = Mock()
    monkeypatch.setattr("geo_blog.slack_guard.WebClient", lambda token: client)
    auto.migrate_pending(s, "draft")
    assert client.chat_update.call_count == 2
    client.chat_postMessage.assert_not_called()
    migrated = json.loads((tmp_path / "production-review.json").read_text())
    assert migrated["state"] == "queued" and migrated["publication_policy"] == auto.POLICY


def test_undo_merges_only_pinned_revert_and_verifies_removal(tmp_path, monkeypatch):
    s = settings(tmp_path)
    path = record(s, existing=True)
    data = json.loads(path.read_text())
    data.update(state="undo_ready", undo_head="undo-head", undo_base="base", undo_pr=99)
    path.write_text(json.dumps(data))
    (tmp_path / "first" / "undo-website").mkdir(parents=True)
    (tmp_path / "production-review.json").write_text(
        json.dumps({"draft_id": "newer", "state": "published"})
    )
    calls = []

    def api(checkout, endpoint, *args):
        calls.append((endpoint, args))
        if endpoint.endswith("/main"):
            return {"object": {"sha": "base"}}
        if endpoint.endswith("/merge"):
            return {"merged": True, "sha": "undo-merged"}
        return {
            "head": {"sha": "undo-head", "repo": {"full_name": pub.REPOSITORY}},
            "base": {"ref": "main", "repo": {"full_name": pub.REPOSITORY}},
            "state": "open",
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
        }

    monkeypatch.setattr(pub, "api", api)
    monkeypatch.setattr(pub, "wait_production", Mock())
    monkeypatch.setattr(undo, "require_ci_quality", Mock())
    monkeypatch.setattr(undo, "complete_source", Mock())
    monkeypatch.setattr("geo_blog.research_queue.live_paths", lambda: {"/blog/another"})
    http = Mock()
    http.__enter__ = Mock(return_value=http)
    http.__exit__ = Mock(return_value=False)
    http.get.return_value.status_code = 404
    monkeypatch.setattr(undo.httpx, "Client", lambda **kwargs: http)
    slack = Mock()
    monkeypatch.setattr("geo_blog.slack_guard.WebClient", lambda token: slack)
    undo.run_pending(s)
    assert json.loads(path.read_text())["state"] == "unpublished"
    assert json.loads((tmp_path / "production-review.json").read_text())["draft_id"] == "newer"
    merges = [args for endpoint, args in calls if endpoint.endswith("/merge")]
    assert len(merges) == 1 and "sha=undo-head" in merges[0]
    undo.complete_source.assert_called_once()
    slack.chat_update.assert_called_once()
