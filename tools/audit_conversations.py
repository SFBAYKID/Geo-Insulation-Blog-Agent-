"""Synthetic conversation audit through Claude Message Batches only.

Default: prepare requests and print a cost estimate without calling Claude.
--submit --max-estimated-usd N submits one round after reviewing the estimate.
--collect downloads results; rerun the default command to plan the next round.
Up to four depenair leak rounds. Results require human review, not automatic grading.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from geo_blog.settings import Settings
    from geo_blog.store import Store

import argparse
import json
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path.cwd()))
from anthropic import Anthropic
from anthropic.types import Message

from geo_blog import batch
from geo_blog.conversation import conversation_turns, initialize
from geo_blog.settings import Settings
from geo_blog.store import Store

payload = {
    "markdown": "# Email replies with review\nA fictional customer asks about an invoice. Read the email, draft a reply, then let a person edit, send or escalate. Interactive send/edit/escalate example. Geo Insulation provides home repair services.",
    "front_matter": {"title": "AI Email Reply: Draft, Approve, Send and Escalate"},
    "topic": {
        "keyword": "ai email reply",
        "secondary_keyword": "ai email response generator",
        "product": {
            "name": "Attic Insulation",
            "url": "https://geo-insulation.com/services/attic-insulation",
        },
    },
    "report": {"word_count": 1400},
    "preview_url": "https://example.vercel.app/blog/email-reply",
}


def replay(case: Any, responses: Any, settings: Settings) -> Any:
    """Replay."""
    with tempfile.TemporaryDirectory() as tmp:
        st = Store(tmp)
        st.reserve("nightly-2026-09-15", "synthetic", "synthetic-topic")
        st.save("nightly-2026-09-15", payload)
        initialize(st)
        with st.db() as db:
            db.execute("UPDATE drafts SET status='pending'")
            db.execute("CREATE TABLE competitor_reports (draft_id TEXT, report TEXT)")
            db.execute(
                "INSERT INTO competitor_reports VALUES(?,?)",
                (
                    "nightly-2026-09-15",
                    json.dumps(
                        {
                            "method": "First three eligible Google organic results via SerpApi, excluding ads, forums and videos",
                            "location": "San Antonio, Texas, United States",
                            "pages": [
                                {
                                    "title": f"Example competitor {i}",
                                    "url": f"https://competitor{i}.example/email",
                                    "word_count": i * 500,
                                    "primary_keyword_count": i,
                                    "mobile_performance": 90 - i,
                                }
                                for i in range(1, 4)
                            ],
                            "monthly_search_volume": None,
                        }
                    ),
                ),
            )
        history = []
        if case["id"] in ["B06", "B07", "B09"]:
            history = [
                {
                    "role": "assistant",
                    "content": "The job starts at 9 PM Pacific. That is when writing begins, rather than when the finished preview arrives.",
                }
            ]
        if case["id"] in ["B42", "B67", "B70"]:
            with st.db() as db:
                db.execute(
                    "INSERT INTO blog_revision_requests VALUES(?,?,?,?,?)",
                    (
                        "prior",
                        "nightly-2026-09-15",
                        "owner",
                        "Shorten the opening",
                        "requested",
                    ),
                )
            history = [
                {"role": "user", "content": "Shorten the opening."},
                {
                    "role": "assistant",
                    "content": "I saved your revision request. The article has not changed; rebuilding from chat is not connected yet.",
                },
            ]
        if case["id"] == "B59":
            history = [
                {"role": "user", "content": "Can you shorten the opening?"},
                {
                    "role": "assistant",
                    "content": "Yes, I can save that revision request. The article will still need a manual edit.",
                },
            ]
        trace = []
        turns = conversation_turns(
            settings,
            st,
            st.get("nightly-2026-09-15"),
            case["message"],
            history,
            case["id"],
            "owner",
            trace=trace,
        )
        request = next(turns)
        for raw in responses:
            try:
                request = turns.send(Message.model_validate(raw))
            except StopIteration as done:
                assert st.get("nightly-2026-09-15")["status"] == "pending"
                assert json.loads(st.get("nightly-2026-09-15")["payload"]) == payload
                return None, dict(case, status="response_recorded", reply=done.value, tools=trace)
        return request, None


def main(argv: Any = None) -> None:
    """Parse explicit command options and run the requested operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", help="Comma-separated case IDs for a targeted run")
    parser.add_argument("--output", default="storage/blog-conversation-batch-audit")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--submit", action="store_true")
    actions.add_argument("--collect", action="store_true")
    parser.add_argument("--max-estimated-usd", type=float)
    options = parser.parse_args(argv)
    root = Path(options.output)
    root.mkdir(parents=True, exist_ok=True)
    # Synthetic tooling must never enqueue real revisions or contact Slack/Airtable.
    settings = Settings().model_copy(
        update={
            "revisions_enabled": False,
            "publishing_enabled": False,
            "storage_dir": root,
        }
    )
    path = root / "cases.json"
    if path.exists():
        cases = json.loads(path.read_text())
        if options.ids and {c["id"] for c in cases} != set(options.ids.split(",")):
            raise ValueError("Use another output directory for different cases")
    else:
        cases = json.loads(Path("tests/fixtures/blog_conversation_cases.json").read_text())
        if options.ids:
            cases = [c for c in cases if c["id"] in options.ids.split(",")]
        if not cases:
            raise ValueError("No matching audit cases")
        path.write_text(json.dumps(cases, indent=2))
    prior = sorted(root.glob("round-*/batch.json"))
    if options.collect:
        if not prior:
            raise ValueError("No batch has been submitted")
        client = Anthropic(api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0)
        results = batch.collect(client, settings, prior[-1].parent)
        print(
            "Batch still processing; no new requests submitted."
            if results is None
            else "Batch results saved. Run without flags to plan the next round."
        )
        return
    responses = {c["id"]: [] for c in cases}
    errors = {}
    spent_estimate = 0
    for manifest in prior:
        state = json.loads(manifest.read_text())
        spent_estimate += state["estimate"]["planning_estimate_usd"]
        result_path = manifest.parent / "results.json"
        if not result_path.exists():
            print("A batch is pending. Use --collect; do not resubmit.")
            return
        for key, result in json.loads(result_path.read_text()).items():
            if result["type"] == "succeeded":
                responses[key].append(result["message"])
            else:
                errors[key] = result["type"]
    requests = []
    finished = []
    with patch(
        "geo_blog.topics.select_topic",
        return_value={
            "keyword": "AI task automation",
            "secondary_keyword": "human approval",
            "queue_remaining_after_selection": 11,
        },
    ):
        for case in cases:
            if case["id"] in errors:
                finished.append(dict(case, status="error", error_type=errors[case["id"]]))
                continue
            params, result = replay(case, responses[case["id"]], settings)
            if result:
                finished.append(result)
            else:
                requests.append({"custom_id": case["id"], "params": params})
    (root / "results.json").write_text(json.dumps(finished, indent=2))
    if not requests:
        print(f"{len(finished)} results recorded; human review required.")
        return
    if len(prior) >= 4:
        raise ValueError("Four-round limit reached; no further paid requests")
    quote = batch.estimate(requests)
    quote.update(
        round=len(prior) + 1,
        prior_planning_estimate_usd=round(spent_estimate, 4),
        remaining_rounds_at_most=4 - len(prior),
    )
    # Re-estimate each depenair leak round once actual tool results are known.
    (root / "estimate.json").write_text(json.dumps(quote, indent=2))
    print(json.dumps(quote, indent=2), flush=True)
    if not options.submit:
        return
    if options.max_estimated_usd is None:
        raise ValueError("Review the estimate and supply --max-estimated-usd for the whole audit")
    remaining = options.max_estimated_usd - spent_estimate
    client = Anthropic(api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0)
    state = batch.submit(client, requests, root / f"round-{len(prior) + 1}", remaining)
    print(json.dumps({"batch_id": state["batch_id"], "state": state["state"]}))


if __name__ == "__main__":
    main()
