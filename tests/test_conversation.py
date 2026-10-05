from unittest.mock import Mock, patch

from geo_blog.conversation import handle, initialize, tool_result
from geo_blog.settings import Settings
from geo_blog.store import Store


def fixture(tmp_path):
    s = Settings(_env_file=None, slack_approver_ids="owner")
    st = Store(tmp_path)
    st.reserve("draft", "test", "topic")
    st.save(
        "draft",
        {
            "markdown": "Original article",
            "front_matter": {"title": "Original"},
            "topic": {},
        },
    )
    st.set_thread("draft", s.slack_channel_id, "100.1")
    event = {
        "channel": s.slack_channel_id,
        "thread_ts": "100.1",
        "ts": "101.1",
        "user": "owner",
        "text": "Cool",
    }
    return s, st, event


def test_only_owner_thread_messages_are_answered_once(tmp_path):
    s, st, e = fixture(tmp_path)
    c = Mock()
    with patch("geo_blog.conversation.answer", return_value="Glad that helps!") as answer:
        handle(s, st, dict(e, user="stranger"), c, "bot")
        handle(s, st, dict(e, bot_id="bot"), c, "bot")
        handle(s, st, dict(e, thread_ts="unrelated"), c, "bot")
        handle(s, st, e, c, "bot")
        handle(s, st, e, c, "bot")
        assert answer.call_count == 1
    assert c.chat_postMessage.call_count == 1
    assert c.chat_postMessage.call_args.kwargs["thread_ts"] == "100.1"
    assert not c.chat_postMessage.call_args.kwargs["reply_broadcast"]


def test_context_is_thread_scoped(tmp_path):
    s, st, e = fixture(tmp_path)
    c = Mock()
    with patch("geo_blog.conversation.answer", return_value="A reply") as answer:
        handle(s, st, e, c, "bot")
        handle(s, st, dict(e, ts="102.1", text="What did you mean?"), c, "bot")
        assert answer.call_args.args[4] == [
            {"role": "user", "content": "Cool"},
            {"role": "assistant", "content": "A reply"},
        ]
        handle(s, st, dict(e, thread_ts="200.1", ts="201.1", text="<@bot> hello"), c, "bot")
        assert answer.call_args.args[4] == []
        handle(s, st, dict(e, thread_ts="200.1", ts="202.1", text="Cool"), c, "bot")
        assert answer.call_args.args[4] == [
            {"role": "user", "content": "<@bot> hello"},
            {"role": "assistant", "content": "A reply"},
        ]


def test_revision_note_is_idempotent_and_does_not_edit_or_approve(tmp_path):
    s, st, e = fixture(tmp_path)
    initialize(st)
    row = st.get("draft")
    before = dict(row)
    kwargs = dict(settings=s, store=st, row=row, event_key="event", user="owner")
    for _ in range(2):
        r = tool_result("record_revision_request", {"request": "Shorten the opening"}, **kwargs)
    assert r["saved"] and not r["article_changed"]
    assert st.get("draft") == before
    with st.db() as db:
        assert db.execute("SELECT COUNT(*) FROM blog_revision_requests").fetchone()[0] == 1
    assert tool_result("delete_database", {}, **kwargs) == {"error": "Unsupported tool"}
    assert tool_result("blog_status", {}, **kwargs)["revision_requests"] == [
        {"request": "Shorten the opening", "status": "requested"}
    ]


def test_ambiguous_delivery_is_not_resent(tmp_path):
    s, st, e = fixture(tmp_path)
    c = Mock()
    c.chat_postMessage.side_effect = TimeoutError()
    with patch("geo_blog.conversation.answer", return_value="Hello"):
        import pytest

        with pytest.raises(TimeoutError):
            handle(s, st, e, c, "bot")
        handle(s, st, e, c, "bot")
    assert c.chat_postMessage.call_count == 1


def test_revision_write_response_cannot_promise_a_rebuild(tmp_path):
    from types import SimpleNamespace

    from geo_blog.conversation import answer

    s, st, e = fixture(tmp_path)
    initialize(st)
    call = Mock(
        type="tool_use",
        name="record_revision_request",
        input={"request": "Shorten the opening"},
        id="tool-1",
    )
    call.name = "record_revision_request"
    call.model_dump.return_value = {
        "type": "tool_use",
        "id": "tool-1",
        "name": "record_revision_request",
        "input": call.input,
    }
    model = Mock()
    model.messages.create.return_value = SimpleNamespace(content=[call], stop_reason="tool_use")
    reply = answer(s, st, st.get("draft"), "Shorten the opening", [], "event", "owner", model=model)
    assert "article has not changed" in reply and "no rebuild has been scheduled" in reply
    assert model.messages.create.call_count == 1
