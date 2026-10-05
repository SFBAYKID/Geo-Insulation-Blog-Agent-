import json
from unittest.mock import Mock

import pytest
from anthropic.types import Message

from geo_blog import batch
from geo_blog.claude_usage import CACHE, cold_estimate, record_usage
from geo_blog.content import Writer
from geo_blog.settings import Settings


def response():
    return Message(
        id="msg-test",
        type="message",
        role="assistant",
        model="claude-sonnet-4-6",
        content=[{"type": "text", "text": "Done"}],
        stop_reason="end_turn",
        usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 4000,
        },
    )


def test_writer_keeps_prefix_and_tools_stable_when_feedback_changes(tmp_path):
    writer = Writer(Settings(_env_file=None, storage_dir=tmp_path), client=Mock())
    a = writer.request(
        "Long system",
        "First correction",
        cache_parts=["Source packet", "Saved article"],
    )
    b = writer.request(
        "Long system",
        "Different correction",
        cache_parts=["Source packet", "Saved article"],
    )
    assert a["system"] == b["system"] and a["system"][0]["cache_control"] == CACHE
    assert a["messages"][0]["content"][:2] == b["messages"][0]["content"][:2]
    assert all(block["cache_control"] == CACHE for block in a["messages"][0]["content"][:2])
    assert "cache_control" not in b["messages"][0]["content"][-1]
    research = writer.request("Research", "Search", research=True)
    assert research["tools"] == writer.request("Other research", "Search", research=True)["tools"]
    assert research["tool_choice"] == {"type": "auto"}
    research["tools"][0]["max_uses"] = 999
    assert writer.request("System", "Next", research=True)["tools"][0]["max_uses"] == 6
    with pytest.raises(ValueError):
        writer.request("System", "Next", tools=[])


def test_writing_and_review_calls_offer_no_tool_the_model_cannot_use(tmp_path):
    """A defined but forbidden search tool made the writer announce a search and return no article."""
    writer = Writer(Settings(_env_file=None, storage_dir=tmp_path), client=Mock())
    for params in (
        writer.request("Writer", "Write", cache_parts=["Evidence"]),
        writer.request("Editor", "Review", cache_parts=["Evidence", "Draft"]),
        writer.request("Visual planner", "Plan"),
    ):
        assert "tools" not in params and "tool_choice" not in params
    assert cold_estimate(writer.request("Writer", "Write"))["planning_estimate_usd"] > 0


def test_usage_receipt_retains_real_cache_counters_without_prompts(tmp_path):
    s = Settings(_env_file=None, storage_dir=tmp_path)
    params = {
        "model": s.writer_model,
        "max_tokens": 20,
        "system": "private source",
        "messages": [],
        "tools": [],
    }
    receipt = record_usage(s, response(), params)
    assert receipt["cache_read_input_tokens"] == 4000
    assert receipt["estimated_cost_usd"] == pytest.approx(0.0006)
    text = (tmp_path / "claude-usage.jsonl").read_text()
    assert "private source" not in text
    assert record_usage(s, response(), params, batch=True)["estimated_cost_usd"] == pytest.approx(
        0.0003
    )


def test_batch_budget_duplicate_and_ambiguous_submission(tmp_path):
    requests = [
        {
            "custom_id": "one",
            "params": {"model": "claude-sonnet-4-6", "max_tokens": 20, "messages": []},
        }
    ]
    client = Mock()
    client.messages.batches.create.return_value.id = "batch-one"
    with pytest.raises(ValueError, match="budget"):
        batch.submit(client, requests, tmp_path / "small", 0)
    client.messages.batches.create.assert_not_called()
    first = batch.submit(client, requests, tmp_path / "good", 1)
    assert batch.submit(client, requests, tmp_path / "good", 1) == first
    assert client.messages.batches.create.call_count == 1
    client.messages.create.assert_not_called()
    client.messages.batches.create.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        batch.submit(client, requests, tmp_path / "ambiguous", 1)
    with pytest.raises(RuntimeError, match="ambiguous"):
        batch.submit(client, requests, tmp_path / "ambiguous", 1)
    assert client.messages.batches.create.call_count == 2


def test_batch_collection_preserves_failures_and_never_resends(tmp_path):
    from anthropic.types.messages import MessageBatchIndividualResponse

    s = Settings(_env_file=None, storage_dir=tmp_path)
    client = Mock()
    client.messages.batches.retrieve.return_value.processing_status = "ended"
    requests = [
        {
            "custom_id": name,
            "params": {"model": s.writer_model, "max_tokens": 20, "messages": []},
        }
        for name in ["one", "two"]
    ]
    (tmp_path / "requests.json").write_text(json.dumps(requests))
    (tmp_path / "batch.json").write_text(json.dumps({"batch_id": "batch-one"}))
    client.messages.batches.results.return_value = [
        MessageBatchIndividualResponse(
            custom_id="one", result={"type": "succeeded", "message": response()}
        ),
        MessageBatchIndividualResponse(custom_id="two", result={"type": "expired"}),
    ]
    result = batch.collect(client, s, tmp_path)
    assert result["one"]["message"]["usage"]["cache_read_input_tokens"] == 4000
    assert result["two"]["type"] == "expired"
    assert batch.collect(client, s, tmp_path) == result
    client.messages.batches.results.assert_called_once()
    client.messages.batches.create.assert_not_called()
    client.messages.create.assert_not_called()


def test_conversation_tool_results_are_cached_with_unchanged_tool_list(tmp_path):
    from geo_blog.conversation import conversation_turns, initialize
    from geo_blog.store import Store

    s = Settings(_env_file=None, storage_dir=tmp_path)
    st = Store(tmp_path)
    initialize(st)
    st.reserve("test", "test", "test")
    st.save("test", {"markdown": "Saved article", "front_matter": {}, "topic": {}})
    turns = conversation_turns(s, st, st.get("test"), "Read the article", [], "evt", "owner")
    first = next(turns)
    call = Message(
        id="tool",
        type="message",
        role="assistant",
        model=s.writer_model,
        stop_reason="tool_use",
        content=[{"type": "tool_use", "id": "t1", "name": "read_article", "input": {}}],
        usage={"input_tokens": 1, "output_tokens": 1},
    )
    second = turns.send(call)
    assert first["tools"] == second["tools"]
    assert second["cache_control"] == CACHE
    assert second["system"][0]["cache_control"] == CACHE
    assert "Saved article" in second["messages"][-1]["content"][0]["content"]


def test_offline_audit_uses_same_turns_without_sync_api(tmp_path):
    import runpy

    replay = runpy.run_path("tools/audit_conversations.py")["replay"]
    s = Settings(_env_file=None, storage_dir=tmp_path)
    case = {"id": "B01", "message": "Cool"}
    request, result = replay(case, [], s)
    assert result is None and request["cache_control"] == CACHE
    request, result = replay(case, [response().model_dump(mode="json")], s)
    assert request is None and result["status"] == "response_recorded"


def test_workflow_estimate_discloses_retries_and_does_not_assume_hits():
    from geo_blog.claude_usage import workflow_estimate, workflow_estimate_text
    from geo_blog.slack_app import topic_message

    nightly = workflow_estimate("claude-sonnet-4-6")
    assert nightly["first_pass_calls"] == 6 and nightly["all_retries_calls"] == 30
    assert nightly["first_pass_estimate_usd"] < nightly["all_retries_estimate_usd"]
    assert not nightly["cache_savings_assumed"]
    text = topic_message({"claude_cost_estimate": nightly})
    assert workflow_estimate_text(nightly) not in text
    assert "Artwork is separate" in workflow_estimate_text(nightly)
    assert workflow_estimate("claude-sonnet-4-6", revision=True)["all_retries_calls"] == 6
    assert workflow_estimate("claude-sonnet-4-6", include_visuals=False)["all_retries_calls"] == 30
    with pytest.raises(ValueError, match="pricing"):
        workflow_estimate("unknown-model")
