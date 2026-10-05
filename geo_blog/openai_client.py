"""OpenAI Responses transport with bounded tools, strict JSON and no hidden retries."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .model_response import Block, Message, Usage
from .settings import Settings


def request_body(params: dict[str, Any]) -> dict[str, Any]:
    """Translate the agent's saved request contract into the Responses API."""
    allowed = {"model", "max_tokens", "system", "messages", "tools", "tool_choice", "output_config"}
    if set(params) - allowed:
        raise ValueError("Unsupported model request options")
    inputs: list[dict[str, Any]] = []
    for message in params.get("messages", []):
        content = message["content"]
        if isinstance(content, str):
            inputs.append({"role": message["role"], "content": content})
            continue
        for block in content:
            if block["type"] == "text":
                inputs.append({"role": message["role"], "content": block["text"]})
            elif block["type"] == "tool_use":
                inputs.append(
                    {
                        "type": "function_call",
                        "call_id": block["id"],
                        "name": block["name"],
                        "arguments": json.dumps(block["input"]),
                    }
                )
            elif block["type"] == "tool_result":
                inputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": block["tool_use_id"],
                        "output": block["content"],
                    }
                )
            else:
                raise ValueError("Unsupported conversation block")
    system = params.get("system", [])
    body: dict[str, Any] = {
        "model": params["model"],
        "max_output_tokens": params["max_tokens"],
        "instructions": system
        if isinstance(system, str)
        else "\n\n".join(b["text"] for b in system),
        "input": inputs,
        "store": False,
        "reasoning": {"effort": "none"},
    }
    tools: list[dict[str, Any]] = []
    for tool in params.get("tools", []):
        if tool.get("name") == "web_search":
            tools.append(
                {"type": "web_search", "filters": {"allowed_domains": tool["allowed_domains"]}}
            )
            body["max_tool_calls"] = tool["max_uses"]
            body["include"] = ["web_search_call.action.sources"]
        else:
            tools.append(
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                    "strict": True,
                }
            )
    if tools:
        body["tools"] = tools
    if "tool_choice" in params:
        choice = params["tool_choice"]["type"]
        if choice not in {"auto", "none", "required"}:
            raise ValueError("Unsupported tool choice")
        body["tool_choice"] = choice
    if "output_config" in params:
        fmt = params["output_config"]["format"]
        if fmt["type"] != "json_schema":
            raise ValueError("Only JSON schema output is supported")
        body["text"] = {
            "format": {
                "type": "json_schema",
                "name": "blog_result",
                "schema": fmt["schema"],
                "strict": True,
            }
        }
    return body


def normalize_response(raw: dict[str, Any]) -> Message:
    """Preserve provider citations and reject incomplete/refused text downstream."""
    content: list[dict[str, Any]] = []
    searches = 0
    refused = False
    for item in raw.get("output", []):
        if item["type"] == "message":
            for block in item.get("content", []):
                if block["type"] == "output_text":
                    citations = [
                        {"type": "web_search_result_location", "url": a["url"]}
                        for a in block.get("annotations", [])
                        if a.get("type") == "url_citation"
                    ]
                    content.append({"type": "text", "text": block["text"], "citations": citations})
                elif block["type"] == "refusal":
                    refused = True
        elif item["type"] == "function_call":
            arguments = json.loads(item["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            content.append(
                {
                    "type": "tool_use",
                    "id": item["call_id"],
                    "name": item["name"],
                    "input": arguments,
                }
            )
        elif item["type"] == "web_search_call":
            searches += 1
            for source in item.get("action", {}).get("sources", []):
                if source.get("url"):
                    content.append({"type": "web_search_result", "url": source["url"]})
    status = raw.get("status", "unknown")
    stop = "end_turn" if status == "completed" else status
    if refused:
        stop = "refusal"
    elif status == "completed" and any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"
    usage = raw.get("usage")
    return Message(
        id=raw["id"],
        model=raw["model"],
        content=[Block.model_validate(b) for b in content],
        stop_reason=stop,
        usage=Usage.model_validate(
            {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cache_read_input_tokens": (usage.get("input_tokens_details") or {}).get(
                    "cached_tokens", 0
                ),
                "web_search_requests": searches,
            }
        )
        if usage
        else None,
    )


class OpenAIClient:
    """One explicit HTTP request per call; transport failures are never retried."""

    def __init__(self, settings: Settings, timeout: float = 600, *, transport: Any = None) -> None:
        settings.require("openai_api_key")
        self.http = httpx.Client(
            base_url="https://api.openai.com/v1/",
            timeout=timeout,
            headers={"Authorization": "Bearer " + settings.openai_api_key.get_secret_value()},
            transport=transport,
        )
        self.messages = self
        from .openai_batch import OpenAIBatches

        self.batches = OpenAIBatches(self)

    def create(self, **params: Any) -> Message:
        """Generate text or tool calls using the supplied bounded request."""
        response = self.http.post("responses", json=request_body(params))
        response.raise_for_status()
        return normalize_response(response.json())

    def close(self) -> None:
        """Release the HTTP connection pool."""
        self.http.close()


def make_client(settings: Settings, timeout: float = 600) -> OpenAIClient:
    """Create the single provider used for writing, editing and conversation."""
    return OpenAIClient(settings, timeout)
