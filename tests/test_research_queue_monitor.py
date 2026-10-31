"""Research queue notifications never claim rows or confuse access errors with exhaustion."""

from unittest.mock import Mock

import httpx
import pytest

from geo_blog import research_queue_monitor as monitor
from geo_blog.settings import Settings


def settings(tmp_path):
    return Settings(
        _env_file=None,
        storage_dir=tmp_path,
        research_queue_monitor_enabled=True,
        airtable_token="test-only",
        airtable_base_id=monitor.BASE,
        airtable_table=monitor.TABLE,
        slack_team_id="T_TEST",
    )


def test_all_pages_exact_proposed_and_no_writes(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.params.get("offset"):
            return httpx.Response(
                200,
                json={
                    "records": [
                        {"id": "rec3", "fields": {"Status": "Proposed"}},
                        {"id": "rec4", "fields": {"Status": "Claimed"}},
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "records": [
                    {"id": "rec1", "fields": {"Status": "Proposed"}},
                    {"id": "rec2", "fields": {"Status": "Held"}},
                    {"id": "rec5", "fields": {"Status": "Ready"}},
                    {"id": "rec6", "fields": {}},
                ],
                "offset": "next",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert monitor.proposed_count(settings(tmp_path), client) == 2
    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize("count,expected", [(3, 0), (2, 1), (1, 1), (0, 1)])
def test_low_empty_and_deduplication(tmp_path, monkeypatch, count, expected):
    monkeypatch.setattr(monitor, "proposed_count", lambda *args: count)
    slack = Mock()
    slack.auth_test.return_value = {"team_id": "T_TEST"}
    slack.chat_postMessage.return_value = {"ts": "123"}
    config = settings(tmp_path)
    monitor.check_queue(config, slack=slack)
    monitor.check_queue(config, slack=slack)
    assert slack.chat_postMessage.call_count == expected
    if expected:
        kwargs = slack.chat_postMessage.call_args.kwargs
        assert kwargs["channel"] == config.slack_test_channel_id
        assert "<@U01DPJVURHU>" in kwargs["text"]
        assert ("queue is empty" in kwargs["text"]) == (count == 0)


def test_access_failure_is_not_an_empty_queue(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    slack = Mock()
    slack.auth_test.return_value = {"team_id": "T_TEST"}
    slack.chat_postMessage.return_value = {"ts": "123"}
    monitor.check_queue(settings(tmp_path), client=client, slack=slack)
    text = slack.chat_postMessage.call_args.kwargs["text"]
    assert "does not mean it is empty" in text
    assert "test-only" not in text


def test_disabled_monitor_makes_no_calls(tmp_path, monkeypatch):
    read = Mock()
    monkeypatch.setattr(monitor, "proposed_count", read)
    config = settings(tmp_path).model_copy(update={"research_queue_monitor_enabled": False})
    monitor.check_queue(config)
    read.assert_not_called()


def test_ambiguous_send_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "proposed_count", lambda *args: 0)
    slack = Mock()
    slack.auth_test.return_value = {"team_id": "T_TEST"}
    slack.chat_postMessage.side_effect = TimeoutError()
    config = settings(tmp_path)
    monitor.check_queue(config, slack=slack)
    monitor.check_queue(config, slack=slack)
    assert slack.chat_postMessage.call_count == 1
