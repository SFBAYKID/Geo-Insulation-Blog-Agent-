"""Prompt-free OpenAI token receipts and conservative workflow estimates."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings

import copy
import hashlib
import json
import os
from datetime import datetime, timezone

# Standard short-context pricing, verified October 4, 2026.
# https://developers.openai.com/api/docs/pricing
RATES = {
    "gpt-6-luna": {"input": 0.10, "write": 0.125, "read": 0.01, "output": 0.50},
    "gpt-6.1-sol": {"input": 2.00, "write": 2.50, "read": 0.10, "output": 10.00},
}


def cached_text(text: str) -> Any:
    """Keep stable text prefixes; OpenAI controls automatic caching."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("A text block must contain nonempty text")
    return {"type": "text", "text": text}


def fingerprint(value: Any) -> str:
    """Hash canonical JSON for identity checks without exposing the underlying value."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def stable_tools(tools: Any) -> Any:
    """Copy tool definitions so feedback cannot mutate future requests."""
    return copy.deepcopy(tools)


def usage_receipt(response: Any, params: dict[str, Any], *, batch: bool = False) -> Any:
    """Record actual counters and conservative cost without prompts or secrets."""
    usage = getattr(response, "usage", None)
    if not usage or not isinstance(getattr(usage, "input_tokens", None), int):
        return None
    values = usage.model_dump(mode="json")
    rate = RATES.get(params["model"])
    read = values.get("cache_read_input_tokens", 0)
    searches = values.get("web_search_requests", 0)
    cost = None
    if rate and values["input_tokens"] <= 120000:
        # Responses usage may omit separately classified cache writes. Price all
        # noncached tokens at the higher write rate instead of understating cost.
        tokens = (
            max(0, values["input_tokens"] - read) * rate["write"]
            + read * rate["read"]
            + values["output_tokens"] * rate["output"]
        ) / 1e6
        cost = round(tokens * (0.5 if batch else 1) + searches * 0.01, 8)
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "response_id": response.id,
        "provider": "openai",
        "model": params["model"],
        "batch": batch,
        "tools_sha256": fingerprint(params.get("tools", [])),
        "input_tokens": values["input_tokens"],
        "output_tokens": values["output_tokens"],
        "cache_read_input_tokens": read,
        "cost_is_upper_estimate": True,
        "web_search_requests": searches,
        "estimated_cost_usd": cost,
        "assumptions": "Standard short-context pricing; noncached input priced at cache-write rate. Output includes reasoning. Images excluded.",
    }


def record_usage(
    settings: Settings,
    response: Any,
    params: dict[str, Any],
    *,
    batch: bool = False,
    run_id: str | None = None,
    stage: str | None = None,
) -> Any:
    """Append a private usage record without storing prompts or credentials."""
    receipt = usage_receipt(response, params, batch=batch)
    if receipt is not None:
        if run_id is not None:
            receipt["run_id"] = run_id
        if stage is not None:
            receipt["stage"] = stage
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            settings.storage_dir / "model-usage.jsonl",
            os.O_CREAT | os.O_APPEND | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(fd, (json.dumps(receipt) + "\n").encode())
        finally:
            os.close(fd)
    return receipt


def cold_estimate(params: dict[str, Any], *, calls: int = 1, batch: bool = False) -> Any:
    """Conservative planning estimate, not a guaranteed provider invoice cap.

    UTF-8 bytes overestimate ordinary English input tokens. Assume every input
    token incurs a cache write and every output reaches max_tokens. Never
    promise cache savings before usage proves them.
    """
    rate = RATES.get(params["model"])
    if not rate:
        raise ValueError("Verify pricing for this model before estimating a paid run")
    size = (
        len(
            json.dumps(
                {k: params[k] for k in ("system", "tools", "messages") if k in params},
                ensure_ascii=False,
            ).encode()
        )
        + 2000
    )
    cost = (size * rate["write"] + params["max_tokens"] * rate["output"]) / 1_000_000
    searches = sum(
        t.get("max_uses", 0) for t in params.get("tools", []) if t.get("name") == "web_search"
    )
    if params.get("tool_choice", {}).get("type") == "none":
        searches = 0
    return {
        "calls": calls,
        "planning_estimate_usd": round(calls * (cost * (0.5 if batch else 1) + searches * 0.01), 4),
        "batch": batch,
        "assumptions": "UTF-8 byte estimate plus tool overhead; cold cache writes; maximum output; no cache-hit savings assumed. Search-result tokens may add cost.",
    }


def workflow_estimate(
    model: str,
    *,
    revision: bool = False,
    context_bytes: int = 0,
    include_visuals: bool = True,
) -> Any:
    """Disclose assumptions before a multi-stage run; this is not a spend cap.

    Future model output and search packets cannot be counted in advance. Use a
    16k input-token planning allowance per call, increased for known large input.
    Budget cold cache writes and full output limits; hits lower the cost.
    """
    rate = RATES.get(model)
    if not rate:
        raise ValueError("Verify model pricing before starting this workflow")
    n = max(16000, (context_bytes + 2) // 3 + 4000)
    # Nightly: six stages; persistent 30-call maximum across retries.
    # Revisions: at most three patch/editor pairs.
    initial_calls, retry_calls = (2, 6) if revision else (6, 30)
    initial_output, retry_output = (6000, 18000) if revision else (28100, 80100)
    if not revision and not include_visuals:
        initial_calls, retry_calls = 4, 30
        initial_output, retry_output = 22600, 160000
    search = 0 if revision else 0.12

    def price(calls: int, output: int) -> Any:
        """Estimate cold-cache token charges using the stated planning assumptions."""
        return round((calls * n * rate["write"] + output * rate["output"]) / 1e6 + search, 2)

    return {
        "model": model,
        "first_pass_calls": initial_calls,
        "all_retries_calls": retry_calls,
        "first_pass_estimate_usd": price(initial_calls, initial_output),
        "all_retries_estimate_usd": price(retry_calls, retry_output),
        "input_tokens_per_call_assumption": n,
        "cache_savings_assumed": False,
        "includes": "OpenAI text tokens and up to twelve research searches; excludes image generation and hosting",
        "assumptions": "Cold cache writes and maximum outputs. Input size is an allowance, not a cap; actual cost varies with evidence size and retries.",
    }


def workflow_estimate_text(quote: dict[str, Any]) -> str:
    """Explain approximate first-pass and retry costs without promising an invoice cap."""
    return (
        f"Estimated OpenAI text cost: about ${quote['first_pass_estimate_usd']:.2f} for the first pass "
        f"({quote['first_pass_calls']} calls), or about ${quote['all_retries_estimate_usd']:.2f} if all "
        f"{quote['all_retries_calls']} allowed calls are needed. "
        f"Assumes {quote['input_tokens_per_call_assumption']:,} input tokens per call and full outputs; "
        "cache reuse may lower the cost, and larger evidence packets may raise it. Artwork is separate."
    )
