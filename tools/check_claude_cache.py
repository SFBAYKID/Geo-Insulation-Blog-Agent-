"""Two-call cache diagnostic using saved evidence; never changes a draft or Slack.

Default estimates only. --count uses the token-count endpoint (no generation).
After showing the estimate, --run performs exactly two requests, without retries.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from geo_blog.settings import Settings
    from geo_blog.store import Store

import argparse
import json
import sys

sys.path.insert(0, str(Path.cwd()))
from anthropic import Anthropic
from anthropic.types import Message

from geo_blog.claude_usage import RATES, cold_estimate, fingerprint, usage_receipt
from geo_blog.content import PROMPTS, Writer, response_text
from geo_blog.settings import Settings
from geo_blog.store import Store


def main() -> None:
    """Parse explicit command options and run the requested operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft_id")
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--expected-request-sha")
    args = parser.parse_args()
    s = Settings()
    row = Store(s.storage_dir).get(args.draft_id)
    if not row or not row["payload"]:
        raise ValueError("A saved draft is required")
    draft = json.loads(row["payload"])
    folder = s.storage_dir / args.draft_id
    source = json.dumps(
        {
            "research": response_text(
                Message.model_validate_json((folder / "research.json").read_text())
            ),
            "company_pages": json.loads((folder / "company.json").read_text()),
        },
        sort_keys=True,
    )
    # Use actual large prompts and saved data, with a diagnostic-specific task.
    system = (
        (PROMPTS / "writer.md").read_text()
        + "\nFor this transport diagnostic only, do not write or change an article. Respond with ACK."
    )
    client = Anthropic(api_key=s.anthropic_api_key.get_secret_value(), max_retries=0, timeout=120)
    writer = Writer(s, client=client)
    parts = [source, draft["markdown"]]
    requests = [
        writer.request(
            system,
            "Cache diagnostic " + str(i) + ": respond only ACK.",
            max_tokens=16,
            cache_parts=parts,
        )
        for i in [1, 2]
    ]
    key = fingerprint(requests)
    quote = cold_estimate(requests[0], calls=2)
    quote.update(
        requests_sha256=key,
        source_bytes=len(source.encode()),
        article_bytes=len(draft["markdown"].encode()),
        model=s.writer_model,
        maximum_generation_calls=2,
        maximum_output_tokens_per_call=16,
    )
    if args.count:
        # The count endpoint rejects server tools. Count actual text, then add
        # a conservative allowance; actual generation keeps the fixed tool list.
        count = client.messages.count_tokens(
            **{k: v for k, v in requests[0].items() if k in {"model", "system", "messages"}}
        )
        tool_allowance = len(json.dumps(requests[0]["tools"]).encode()) + 1000
        rate = RATES[s.writer_model]
        n = count.input_tokens + tool_allowance
        quote.update(
            counted_text_input_tokens=count.input_tokens,
            tool_allowance_tokens=tool_allowance,
            estimated_usd_with_second_call_cache_hit=round(
                (n * (rate["write_1h"] + rate["read"]) + 32 * rate["output"]) / 1e6, 5
            ),
            estimated_usd_if_both_calls_write_cache=round(
                (n * 2 * rate["write_1h"] + 32 * rate["output"]) / 1e6, 5
            ),
        )
    output = s.storage_dir / "claude-cache-check"
    output.mkdir(parents=True, exist_ok=True)
    saved = output / "estimate.json"
    if args.run and saved.exists():
        earlier = json.loads(saved.read_text())
        if earlier.get("requests_sha256") == key:
            quote = {**earlier, **quote}
    (output / "estimate.json").write_text(json.dumps(quote, indent=2))
    print(json.dumps(quote, indent=2), flush=True)
    if not args.run:
        return
    if args.expected_request_sha != key:
        raise ValueError("Show the estimate first and supply its unchanged request fingerprint")
    if (output / "started.json").exists():
        raise ValueError(
            "Diagnostic already started; inspect existing receipts before another paid run"
        )
    (output / "started.json").write_text(json.dumps({"requests_sha256": key, "calls": 2}))
    receipts = []
    for i in range(2):
        response = writer.call(
            system,
            requests[i]["messages"][0]["content"][-1]["text"],
            max_tokens=16,
            cache_parts=parts,
        )
        receipt = usage_receipt(response, requests[i])
        receipts.append(receipt)
        (output / "usage.json").write_text(json.dumps(receipts, indent=2))
        print(json.dumps(receipt), flush=True)
    assert receipts[1]["cache_read_input_tokens"] > 0, (
        "No cache read observed; do not claim caching works"
    )
    assert receipts[0]["tools_sha256"] == receipts[1]["tools_sha256"]
    assert (
        receipts[0]["cache_creation"].get("ephemeral_1h_input_tokens", 0) > 0
        or receipts[0]["cache_read_input_tokens"] > 0
    )
    print("Verified actual cache read and identical tools; article and Slack unchanged.")


if __name__ == "__main__":
    main()
