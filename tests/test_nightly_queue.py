from unittest.mock import Mock

import httpx
import pytest

from geo_blog.settings import Settings
from geo_blog.slack_app import low_queue_notice
from geo_blog.store import Store
from geo_blog.topics import secondary_keyword_from_brief, select_topic


@pytest.mark.parametrize(
    ("brief", "expected"),
    [
        (
            "MEASURED SUPPORTING KEYWORDS\n\nhand off email template — 210/mo; organic KD 0\n\nShared article: handoff email template; target 2,400 words",
            "hand off email template",
        ),
        (
            "## Supporting keywords\n- human in the loop AI (90/mo); second phrase",
            "human in the loop AI",
        ),
        (
            "Supporting keywords: AI workflow automation — 90/mo",
            "AI workflow automation",
        ),
        ("AI workflow automation; workflow AI", "AI workflow automation"),
        (
            "MEASURED SUPPORTING KEYWORDS\n\nShared article: primary; target 2,400 words",
            "",
        ),
        ("Supporting keywords\nTBD\nCPC unreported", ""),
    ],
)
def test_supporting_phrase_skips_headings_and_metrics(brief, expected):
    assert secondary_keyword_from_brief(brief) == expected


def test_heading_only_is_incomplete_not_a_keyword():
    from geo_blog.topics import IncompleteKeywordQueue

    rows = [
        {
            "id": "bad",
            "fields": {
                "Primary keyword": "handoff email template",
                "Supporting keywords": "MEASURED SUPPORTING KEYWORDS",
                "Status": "Proposed",
            },
        }
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"records": rows}))
    )
    settings = Settings(_env_file=None, airtable_base_id="base", airtable_token="token")
    with pytest.raises(IncompleteKeywordQueue):
        select_topic(settings, set(), client)


def test_view_order_dedup_and_completed_outside_view():
    def record(key, keyword, status="Proposed"):
        return {
            "id": key,
            "fields": {
                "Primary keyword": keyword,
                "Supporting keywords": "support",
                "Status": status,
            },
        }

    rows = [
        record("a", "second"),
        record("z", "first"),
        record("b", "FIRST"),
        record("x", "done"),
        record("outside", "done", "Published"),
    ]

    def handler(request):
        if request.url.params.get("view"):
            assert request.url.params["view"] == "view"
            return httpx.Response(200, json={"records": [rows[i] for i in [1, 0, 2, 3]]})
        return httpx.Response(200, json={"records": rows})

    s = Settings(
        _env_file=None,
        airtable_base_id="base",
        airtable_token="token",
        airtable_view="view",
    )
    topic = select_topic(s, set(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert topic["id"] == "z"
    assert topic["queue_remaining_after_selection"] == 1
    topic = select_topic(s, {"keyword:first"}, httpx.Client(transport=httpx.MockTransport(handler)))
    assert topic["id"] == "a" and topic["queue_remaining_after_selection"] == 0


def test_low_queue_notice_is_threaded_mentions_owner_and_once(tmp_path):
    s = Settings(_env_file=None, slack_approver_ids="owner", queue_low_threshold=5)
    st = Store(tmp_path)
    st.reserve("draft", "day", "topic")
    st.set_thread("draft", s.slack_channel_id, "123.456")
    client = Mock()
    client.auth_test.return_value = {"team_id": s.slack_team_id}
    client.chat_postMessage.return_value = {"ts": "124.456"}
    low_queue_notice(s, st, "draft", {"queue_remaining_after_selection": 6}, client)
    client.chat_postMessage.assert_not_called()
    for _ in range(2):
        low_queue_notice(s, st, "draft", {"queue_remaining_after_selection": 5}, client)
    client.chat_postMessage.assert_called_once()
    args = client.chat_postMessage.call_args.kwargs
    assert args["thread_ts"] == "123.456" and args["reply_broadcast"] is False
    assert "<@owner>" in args["text"] and "5 unused blog topics remain" in args["text"]


def test_held_topics_are_not_reported_as_exhausted():
    import pytest

    from geo_blog.topics import HeldKeywordQueue

    rows = [
        {
            "id": "held",
            "fields": {
                "Primary keyword": "topic",
                "Supporting keywords": "support",
                "Status": "Retarget needed",
            },
        }
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"records": rows}))
    )
    settings = Settings(_env_file=None, airtable_base_id="base", airtable_token="token")
    with pytest.raises(HeldKeywordQueue):
        select_topic(settings, set(), client)
    assert (
        select_topic(
            settings,
            {"held"},
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"records": rows})
                )
            ),
        )
        is None
    )
