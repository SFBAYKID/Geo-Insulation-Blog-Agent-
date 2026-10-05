"""Durable offline OpenAI batches. Never fall back to paid synchronous calls."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .settings import Settings

import fcntl
import json

from .model_usage import cold_estimate, fingerprint, record_usage


def estimate(requests: Any) -> Any:
    """Estimate."""
    rows = [cold_estimate(r["params"], batch=True) for r in requests]
    return {
        "requests": len(requests),
        "planning_estimate_usd": round(sum(r["planning_estimate_usd"] for r in rows), 4),
        "assumptions": "Cold cache writes, conservative UTF-8 input estimate, full output allowance, Batch token discount; cache hits may lower cost.",
    }


def submit(client: Any, requests: Any, folder: Path, max_estimated_usd: Any) -> Any:
    """Submit."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "submit.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _submit(client, requests, folder, max_estimated_usd)


def _submit(client: Any, requests: Any, folder: Path, max_estimated_usd: Any) -> Any:
    manifest = folder / "batch.json"
    key = fingerprint(requests)
    if manifest.exists():
        state = json.loads(manifest.read_text())
        if state["requests_sha256"] != key:
            raise ValueError("Existing batch has different requests")
        if state.get("batch_id"):
            return state
        raise RuntimeError(
            "Batch submission outcome is ambiguous; inspect the provider before retrying"
        )
    if not requests or len({r["custom_id"] for r in requests}) != len(requests):
        raise ValueError("A batch requires unique request IDs")
    quote = estimate(requests)
    if quote["planning_estimate_usd"] > max_estimated_usd:
        raise ValueError("Batch estimate exceeds the supplied budget")
    (folder / "requests.json").write_text(json.dumps(requests, indent=2))
    state = {
        "state": "submitting",
        "requests_sha256": key,
        "estimate": quote,
        "budget_usd": max_estimated_usd,
    }
    # Exclusive creation prevents two operator processes submitting twice.
    with manifest.open("x") as out:
        out.write(json.dumps(state, indent=2))
    response = client.messages.batches.create(requests=requests)
    state.update(state="submitted", batch_id=response.id)
    manifest.write_text(json.dumps(state, indent=2))
    return state


def collect(client: Any, settings: Settings, folder: Path) -> Any:
    """Collect."""
    folder = Path(folder)
    manifest = folder / "batch.json"
    state = json.loads(manifest.read_text())
    if not state.get("batch_id"):
        raise RuntimeError("Batch submission needs reconciliation")
    saved = folder / "results.json"
    if saved.exists():
        return json.loads(saved.read_text())
    current = client.messages.batches.retrieve(state["batch_id"])
    if current.processing_status != "ended":
        return None
    requests = {
        r["custom_id"]: r["params"] for r in json.loads((folder / "requests.json").read_text())
    }
    results = {}
    for item in client.messages.batches.results(state["batch_id"]):
        if item.custom_id not in requests or item.custom_id in results:
            raise ValueError("Unexpected batch result ID")
        results[item.custom_id] = item.result.model_dump(mode="json")
        if item.result.type == "succeeded":
            record_usage(settings, item.result.message, requests[item.custom_id], batch=True)
    if set(results) != set(requests):
        raise ValueError("Batch results are incomplete")
    saved.write_text(json.dumps(results, indent=2))
    state["state"] = "complete"
    manifest.write_text(json.dumps(state, indent=2))
    return results
