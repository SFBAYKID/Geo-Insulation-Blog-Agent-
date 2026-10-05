"""Production decisions require exact message/version and designated reviewers."""

import json
from unittest.mock import Mock

import pytest

from geo_blog.production_review import handle_review
from geo_blog.settings import Settings


@pytest.mark.parametrize(
    "field,value", [("user", "U_OTHER"), ("channel", "C_OTHER"), ("commit", "old"), ("ts", "wrong")]
)
def test_invalid_review_has_no_side_effect(tmp_path, field, value):
    s, body, path = fixture(tmp_path)
    if field == "user":
        body["user"]["id"] = value
    if field == "channel":
        body["channel"]["id"] = value
    if field == "commit":
        body["actions"][0]["value"] = value
    if field == "ts":
        body["message"]["ts"] = value
    client = Mock()
    assert not handle_review(s, body, client)
    assert json.loads(path.read_text())["state"] == "pending"
    client.chat_update.assert_not_called()


def fixture(tmp_path):
    s = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        production_delivery_enabled=True,
        slack_production_channel_id="C_GEO_PRODUCTION_TEST",
        slack_approver_ids="U_GEO_APPROVER_ONE,U_GEO_APPROVER_TWO",
    )
    path = tmp_path / "production-review.json"
    path.write_text(
        json.dumps(
            {"state": "pending", "message_ts": "123", "preview_commit": "commit", "blocks": []}
        )
    )
    body = {
        "team": {"id": s.slack_team_id},
        "channel": {"id": s.slack_production_channel_id},
        "user": {"id": "U_GEO_APPROVER_ONE"},
        "message": {"ts": "123"},
        "actions": [{"action_id": "geo_production_approve", "value": "commit"}],
    }
    return s, body, path


def test_approval_recorded_once_without_publishing(tmp_path):
    s, body, path = fixture(tmp_path)
    client = Mock()
    assert handle_review(s, body, client)
    assert json.loads(path.read_text())["state"] == "approved"
    assert "not live yet" in client.chat_update.call_args.kwargs["text"]
    assert not handle_review(s, body, client)
    assert client.chat_update.call_count == 1


def test_thread_feedback_is_scoped_and_deduplicated(tmp_path):
    from geo_blog.production_review import handle_comment

    s, _, path = fixture(tmp_path)
    saved = json.loads(path.read_text())
    saved["thread_ts"] = "parent"
    path.write_text(json.dumps(saved))
    client = Mock()
    event = {
        "type": "app_mention",
        "channel": s.slack_production_channel_id,
        "thread_ts": "parent",
        "user": "U_GEO_APPROVER_TWO",
        "ts": "note1",
        "text": "Please shorten the intro",
    }
    assert not handle_comment(s, dict(event, thread_ts="other"), client)
    assert handle_comment(s, event, client)
    assert handle_comment(s, event, client)
    assert len(json.loads(path.read_text())["comments"]) == 1
    assert client.chat_postMessage.call_count == 1
    assert client.chat_postMessage.call_args.kwargs["thread_ts"] == "parent"


def test_armed_approval_queues_exact_version(tmp_path):
    from geo_blog.production_publish import comments_digest

    s, body, path = fixture(tmp_path)
    saved = json.loads(path.read_text())
    saved["publication_armed"] = True
    path.write_text(json.dumps(saved))
    client = Mock()
    assert handle_review(s, body, client)
    saved = json.loads(path.read_text())
    assert saved["state"] == "queued"
    assert saved["approved_comments_digest"] == comments_digest(saved)
    assert "Publication is queued" in client.chat_update.call_args.kwargs["text"]
