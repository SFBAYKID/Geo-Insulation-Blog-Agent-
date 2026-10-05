"""Exercise real transport serialization and failure gates with mock HTTP only."""

import json

import httpx
import pytest

from geo_blog.content import EDITOR_FORMAT, Writer, primary_urls, researched_urls, response_text
from geo_blog.conversation import TOOLS
from geo_blog.openai_client import OpenAIClient, normalize_response, request_body
from geo_blog.settings import Settings


def provider_response(status="completed"):
    return {
        "id": "resp_test",
        "model": "gpt-6-luna",
        "status": status,
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Verified text",
                        "annotations": [
                            {"type": "url_citation", "url": "https://www.energy.gov/a"}
                        ],
                    }
                ],
            },
            {
                "type": "web_search_call",
                "action": {"sources": [{"url": "https://www.energystar.gov/b"}]},
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 50},
        },
    }


def test_writer_uses_only_openai_and_preserves_schema_and_receipts(tmp_path):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=provider_response())

    s = Settings(_env_file=None, openai_api_key="test-only", storage_dir=tmp_path)
    client = OpenAIClient(s, transport=httpx.MockTransport(handle))
    writer = Writer(s, client)
    writer.bind_run(tmp_path / "run")
    result = writer.call("Editor", "Review", max_tokens=100, output_config=EDITOR_FORMAT)
    assert response_text(result) == "Verified text"
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://api.openai.com/v1/responses"
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"] == EDITOR_FORMAT["format"]["schema"]
    assert body["max_output_tokens"] == 100 and body["store"] is False
    assert "tools" not in body and "cache_control" not in body and "thinking" not in body
    assert (tmp_path / "model-usage.jsonl").exists()
    client.close()


def test_research_domains_call_bound_and_only_provider_sources_are_trusted():
    writer = Writer(Settings(_env_file=None), client=object())
    body = request_body(writer.request("Research", "Find evidence", research=True))
    assert body["max_tool_calls"] == 6
    assert "energy.gov" in body["tools"][0]["filters"]["allowed_domains"]
    assert body["include"] == ["web_search_call.action.sources"]
    raw = provider_response()
    raw["output"][0]["content"][0]["text"] += " https://www.energy.gov/invented"
    response = normalize_response(raw)
    assert primary_urls(researched_urls(response.model_dump())) == {
        "https://www.energy.gov/a",
        "https://www.energystar.gov/b",
    }
    assert response.usage.web_search_requests == 1


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "unknown"])
def test_incomplete_responses_cannot_pass_as_finished_content(status):
    with pytest.raises(ValueError, match="Incomplete"):
        response_text(normalize_response(provider_response(status)))


def test_refusals_fail_closed():
    raw = provider_response()
    raw["output"][0]["content"] = [{"type": "refusal", "refusal": "No"}]
    with pytest.raises(ValueError, match="refusal"):
        response_text(normalize_response(raw))


def test_function_calls_and_results_keep_ids_without_exec_or_publication_tools():
    raw = provider_response()
    raw["output"] = [
        {"type": "function_call", "call_id": "call_1", "name": "read_article", "arguments": "{}"}
    ]
    response = normalize_response(raw)
    assert response.stop_reason == "tool_use"
    params = {
        "model": "gpt-6-luna",
        "max_tokens": 100,
        "tools": TOOLS,
        "messages": [
            {"role": "assistant", "content": [b.model_dump() for b in response.content]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": "Saved article"}
                ],
            },
        ],
    }
    body = request_body(params)
    assert body["input"][0]["call_id"] == body["input"][1]["call_id"] == "call_1"
    assert body["input"][1]["type"] == "function_call_output"
    assert {t["name"] for t in body["tools"]} == {
        "blog_status",
        "read_article",
        "keyword_queue",
        "record_revision_request",
    }


def test_timeout_never_retries_and_keeps_spending_reservation(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("Unknown outcome")

    client = OpenAIClient(
        Settings(_env_file=None, openai_api_key="test"), transport=httpx.MockTransport(handle)
    )
    writer = Writer(Settings(_env_file=None), client)
    writer.bind_run(tmp_path, max_calls=1)
    with pytest.raises(httpx.ReadTimeout):
        writer.call("Writer", "Draft")
    with pytest.raises(RuntimeError, match="allowance exhausted"):
        writer.call("Writer", "Draft")
    assert len(calls) == 1
    client.close()


def test_unknown_options_rejected_before_network():
    with pytest.raises(ValueError, match="Unsupported"):
        request_body({"model": "gpt-6-luna", "max_tokens": 100, "thinking": {"type": "disabled"}})


def test_openai_batch_upload_and_collection_keep_failure_rows(tmp_path):
    from geo_blog import batch

    seen = []
    rows = [
        {"custom_id": "one", "response": {"status_code": 200, "body": provider_response()}},
        {"custom_id": "two", "response": None, "error": {"code": "batch_expired"}},
    ]

    def handle(request):
        seen.append(request)
        path = request.url.path
        if path == "/v1/files":
            assert b'"url": "/v1/responses"' in request.content
            assert b"max_output_tokens" in request.content
            return httpx.Response(200, json={"id": "file_input"})
        if path == "/v1/batches":
            assert json.loads(request.content)["input_file_id"] == "file_input"
            return httpx.Response(200, json={"id": "batch_test"})
        if path == "/v1/batches/batch_test":
            return httpx.Response(
                200, json={"status": "completed", "output_file_id": "file_output"}
            )
        assert path == "/v1/files/file_output/content"
        return httpx.Response(200, text="\n".join(json.dumps(row) for row in rows))

    s = Settings(_env_file=None, openai_api_key="test", storage_dir=tmp_path)
    client = OpenAIClient(s, transport=httpx.MockTransport(handle))
    requests = [
        {
            "custom_id": name,
            "params": {
                "model": s.writer_model,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Test"}],
            },
        }
        for name in ["one", "two"]
    ]
    batch.submit(client, requests, tmp_path / "batch", 1)
    result = batch.collect(client, s, tmp_path / "batch")
    assert result["one"]["type"] == "succeeded"
    assert result["two"]["error"]["code"] == "batch_expired"
    count = len(seen)
    assert batch.collect(client, s, tmp_path / "batch") == result
    assert len(seen) == count
    assert all(request.url.path != "/v1/responses" for request in seen)
    client.close()


def test_empty_completed_text_cannot_pass_as_finished_content():
    raw = provider_response()
    raw["output"] = []
    with pytest.raises(ValueError, match="Empty"):
        response_text(normalize_response(raw))


def test_users_open_ai_key_alias_is_accepted_without_anthropic(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPEN_AI_KEY=test-only\n")
    settings = Settings(_env_file=env)
    assert settings.openai_api_key.get_secret_value() == "test-only"
    assert "anthropic_api_key" not in Settings.model_fields
