"""Explicit one-hour cache boundaries and prompt-free Claude usage receipts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings

import copy
import hashlib
import json
import os
from datetime import datetime, timezone

CACHE = {"type": "ephemeral", "ttl": "1h"}
# Anthropic direct API, global inference; verified September 17, 2026.
RATES = {
    "claude-haiku-4-5-20251001": {"input": 1, "write_1h": 2, "read": 0.10, "output": 5},
    "claude-sonnet-4-6": {"input": 3, "write_1h": 6, "read": 0.30, "output": 15},
    # Owner switched the writer to Sonnet 5 on September 28, 2026 (Haiku missed exact keywords).
    "claude-sonnet-5": {"input": 2, "write_1h": 4, "read": 0.20, "output": 10},
}
# Sonnet 5 thinks by default and those tokens count against max_tokens, which would
# truncate full articles; every call keeps thinking off, matching the Haiku behavior.
NO_THINKING = {"type": "disabled"}


def cached_text(text: str) -> Any:
    """Attach the configured cache boundary to nonempty stable text."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("A cache block must contain nonempty text")
    return {"type": "text", "text": text, "cache_control": dict(CACHE)}


def fingerprint(value: Any) -> str:
    """Hash canonical JSON for identity checks without exposing the underlying value."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def stable_tools(tools: Any) -> Any:
    """Copy tool definitions and place one stable cache boundary at their end."""
    result = copy.deepcopy(tools)
    if result:
        result[-1]["cache_control"] = dict(CACHE)
    return result


def usage_receipt(response: Any, params: dict[str, Any], *, batch: bool = False) -> Any:
    """Calculate a prompt-free usage receipt from actual provider token counters."""
    usage = getattr(response, "usage", None)
    if not usage or not isinstance(getattr(usage, "input_tokens", None), int):
        return None  # Synthetic unit-test responses may omit usage.
    values = usage.model_dump(mode="json")
    model = params["model"]
    rate = RATES.get(model)
    creation = values.get("cache_creation") or {}
    written = values.get("cache_creation_input_tokens") or 0
    read = values.get("cache_read_input_tokens") or 0
    searches = (values.get("server_tool_use") or {}).get("web_search_requests", 0) or 0
    cost = None
    unknown = max(
        0,
        written
        - sum(
            creation.get(k, 0) or 0
            for k in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
        ),
    )
    if rate:
        # Unclassified provider search cache writes are priced conservatively at 1h.
        # Do not silently omit them or claim this estimate is an invoice.
        five = creation.get("ephemeral_5m_input_tokens", 0) or 0
        hourly = (creation.get("ephemeral_1h_input_tokens", 0) or 0) + unknown
        tokens = (
            values["input_tokens"] * rate["input"]
            + hourly * rate["write_1h"]
            + five * rate["input"] * 1.25
            + read * rate["read"]
            + values["output_tokens"] * rate["output"]
        ) / 1_000_000
        cost = round(tokens * (0.5 if batch else 1) + searches * 0.01, 8)
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "response_id": response.id,
        "model": model,
        "batch": batch,
        "cache_ttl": "1h",
        "tools_sha256": fingerprint(params.get("tools", [])),
        "input_tokens": values["input_tokens"],
        "output_tokens": values["output_tokens"],
        "cache_creation_input_tokens": written,
        "cache_read_input_tokens": read,
        "cache_creation": creation,
        "unclassified_cache_creation_input_tokens": unknown,
        "cost_is_upper_estimate": bool(unknown),
        "web_search_requests": searches,
        "estimated_cost_usd": cost,
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
            settings.storage_dir / "claude-usage.jsonl",
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
    token incurs a 1h cache write and every output reaches max_tokens. Never
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
    cost = (size * rate["write_1h"] + params["max_tokens"] * rate["output"]) / 1_000_000
    searches = sum(
        t.get("max_uses", 0) for t in params.get("tools", []) if t.get("name") == "web_search"
    )
    if params.get("tool_choice", {}).get("type") == "none":
        searches = 0
    return {
        "calls": calls,
        "planning_estimate_usd": round(calls * (cost * (0.5 if batch else 1) + searches * 0.01), 4),
        "batch": batch,
        "assumptions": "UTF-8 byte estimate plus tool overhead; cold 1h writes; maximum output; no cache-hit savings assumed. Search-result tokens may add cost.",
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
    Budget cold hourly cache writes and full output limits; hits lower the cost.
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
        return round((calls * n * rate["write_1h"] + output * rate["output"]) / 1e6 + search, 2)

    return {
        "model": model,
        "first_pass_calls": initial_calls,
        "all_retries_calls": retry_calls,
        "first_pass_estimate_usd": price(initial_calls, initial_output),
        "all_retries_estimate_usd": price(retry_calls, retry_output),
        "input_tokens_per_call_assumption": n,
        "cache_savings_assumed": False,
        "includes": "Claude tokens and up to twelve research searches; excludes image generation and hosting",
        "assumptions": "Cold 1h writes and maximum outputs. Input size is an allowance, not a cap; actual cost varies with evidence size and retries.",
    }


def workflow_estimate_text(quote: dict[str, Any]) -> str:
    """Explain approximate first-pass and retry costs without promising an invoice cap."""
    return (
        f"Estimated Claude cost: about ${quote['first_pass_estimate_usd']:.2f} for the first pass "
        f"({quote['first_pass_calls']} calls), or about ${quote['all_retries_estimate_usd']:.2f} if all "
        f"{quote['all_retries_calls']} allowed calls are needed. "
        f"Assumes {quote['input_tokens_per_call_assumption']:,} input tokens per call and full outputs; "
        "cache reuse may lower the cost, and larger evidence packets may raise it. Artwork is separate."
    )
