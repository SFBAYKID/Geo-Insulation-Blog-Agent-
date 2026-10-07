"""Production-channel chat answers people in thread and never takes actions."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from geo_blog import production_chat
from geo_blog.production_chat import handle, next_weekly_run
from geo_blog.settings import Settings
from geo_blog.slack_guard import safe_client
from geo_blog.store import Store

PROD = "C_PRODUCTION"


def settings(tmp_path, **overrides):
    values = dict(
        _env_file=None,
        storage_dir=tmp_path,
        slack_approver_ids="U_OWNER",
        slack_production_channel_id=PROD,
        production_delivery_enabled=True,
        production_chat_enabled=True,
    )
    return Settings(**(values | overrides))


def event(**overrides):
    return {"channel": PROD, "ts": "200.1", "user": "U_CLIENT", "text": "<@U_BOT> Hi"} | overrides


def test_mentions_from_any_person_get_one_threaded_reply(tmp_path):
    s, store, client = settings(tmp_path), Store(tmp_path), Mock()
    with patch.object(production_chat, "answer", return_value="Hi there.") as answer:
        handle(s, store, event(), client, "U_BOT")
        handle(s, store, event(), client, "U_BOT")
    assert answer.call_count == 1
    sent = client.chat_postMessage.call_args.kwargs
    assert sent["channel"] == PROD and sent["thread_ts"] == "200.1"
    assert not sent["reply_broadcast"]


def test_ignores_bots_unmentioned_strangers_and_disabled_chat(tmp_path):
    store, client = Store(tmp_path), Mock()
    with patch.object(production_chat, "answer", return_value="Hi.") as answer:
        handle(settings(tmp_path), store, event(bot_id="B1"), client, "U_BOT")
        handle(settings(tmp_path), store, event(text="Just chatting"), client, "U_BOT")
        handle(settings(tmp_path, production_chat_enabled=False), store, event(), client, "U_BOT")
        handle(settings(tmp_path), store, event(channel="C_OTHER"), client, "U_BOT")
    assert answer.call_count == 0
    assert client.chat_postMessage.call_count == 0


def test_thread_continues_without_mention_and_keeps_history(tmp_path):
    s, store, client = settings(tmp_path), Store(tmp_path), Mock()
    with patch.object(production_chat, "answer", return_value="Happy to help.") as answer:
        handle(s, store, event(thread_ts="100.1"), client, "U_BOT")
        handle(s, store, event(thread_ts="100.1", ts="201.1", text="Thanks"), client, "U_BOT")
    assert answer.call_args.args[3] == [
        {"role": "user", "content": "<@U_BOT> Hi"},
        {"role": "assistant", "content": "Happy to help."},
    ]
    assert client.chat_postMessage.call_args.kwargs["thread_ts"] == "100.1"


def test_plain_reply_under_an_agent_message_is_answered(tmp_path):
    s, store, client = settings(tmp_path), Store(tmp_path), Mock()
    reply = event(thread_ts="100.1", text="Where do I add it?", parent_user_id="U_BOT")
    with patch.object(production_chat, "answer", return_value="In Basecamp.") as answer:
        handle(s, store, reply, client, "U_BOT")
        handle(
            s,
            store,
            dict(reply, thread_ts="150.1", ts="300.1", parent_user_id="U_PERSON"),
            client,
            "U_BOT",
        )
    assert answer.call_count == 1
    assert client.chat_postMessage.call_args.kwargs["thread_ts"] == "100.1"


def test_model_failure_posts_safe_fallback(tmp_path):
    s, store, client = settings(tmp_path), Store(tmp_path), Mock()
    with patch.object(production_chat, "answer", side_effect=RuntimeError("down")):
        handle(s, store, event(), client, "U_BOT")
    assert "Chase will follow up" in client.chat_postMessage.call_args.kwargs["text"]


def test_answer_is_tool_free_and_grounded_in_status(tmp_path):
    s, store = settings(tmp_path), Store(tmp_path)
    model = Mock()
    model.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="No tasks yet.")]
    )
    with (
        patch.object(production_chat, "queue_status", return_value={"open_blog_tasks": 0}),
        patch.object(production_chat, "record_usage"),
    ):
        assert production_chat.answer(s, store, "Status?", [], model) == "No tasks yet."
    params = model.messages.create.call_args.kwargs
    assert "tools" not in params
    assert '"open_blog_tasks": 0' in params["system"][1]["text"]


def test_guard_allows_only_the_named_production_thread(tmp_path):
    s, raw = settings(tmp_path), Mock()
    safe_client(s, raw, production_thread="100.1").chat_postMessage(
        channel=PROD, thread_ts="100.1", text="Hi."
    )
    assert raw.chat_postMessage.call_count == 1
    with pytest.raises(ValueError):
        safe_client(s, raw, production_thread="100.1").chat_postMessage(channel=PROD, text="Hi.")
    with pytest.raises(ValueError):
        safe_client(s, raw, production_thread="100.1").chat_postMessage(
            channel=PROD, thread_ts="999.9", text="Hi."
        )
    with pytest.raises(ValueError):
        safe_client(s, raw).chat_postMessage(channel=PROD, thread_ts="100.1", text="Hi.")


def test_next_weekly_run_is_wednesday_nine_pacific(tmp_path):
    s = settings(tmp_path)
    after = datetime(2026, 10, 7, 19, 0, tzinfo=timezone.utc)  # Wed noon PDT
    before = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)  # Wed 8 AM PDT
    assert next_weekly_run(s, after) == "Wednesday, October 14 at 9 AM Pacific"
    assert next_weekly_run(s, before) == "Wednesday, October 7 at 9 AM Pacific"


def test_listener_routes_production_events_to_chat_only(tmp_path, monkeypatch):
    from geo_blog import production_publish, slack_app

    s = settings(
        tmp_path,
        slack_listener_enabled=True,
        slack_bot_token="test-token",
        slack_app_token="test-app-token",
        slack_team_id="T_TEST",
    )
    events = {}
    app = Mock()
    app.client.auth_test.return_value = {"team_id": "T_TEST", "user_id": "U_BOT"}
    app.event.side_effect = lambda name: lambda callback: events.setdefault(name, callback)
    monkeypatch.setattr(slack_app, "App", lambda **kwargs: app)
    monkeypatch.setattr(slack_app, "SocketModeHandler", Mock())
    monkeypatch.setattr(production_publish, "start_worker", Mock())
    slack_app.serve(s, Store(tmp_path))
    with (
        patch.object(production_chat, "handle") as chat,
        patch("geo_blog.conversation.handle") as playground,
    ):
        events["app_mention"](event(type="app_mention"), Mock())
    assert chat.call_count == 1 and playground.call_count == 0
