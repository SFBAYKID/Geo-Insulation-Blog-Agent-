"""OpenAI batch transport for offline audits; terminal failures never trigger retries."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Iterator

from .model_response import BatchResult, MessageBatchIndividualResponse

if TYPE_CHECKING:
    from .openai_client import OpenAIClient


class OpenAIBatches:
    """Adapt durable audit requests to uploaded Responses JSONL batches."""

    def __init__(self, client: OpenAIClient) -> None:
        self.client = client

    def create(self, *, requests: list[dict[str, Any]]) -> Any:
        """Upload a bounded request file and submit it once."""
        from .openai_client import request_body

        lines = []
        for request in requests:
            if request["params"].get("tools") and any(
                t.get("name") == "web_search" for t in request["params"]["tools"]
            ):
                raise ValueError("Offline audit batches do not support web research")
            lines.append(
                json.dumps(
                    {
                        "custom_id": request["custom_id"],
                        "method": "POST",
                        "url": "/v1/responses",
                        "body": request_body(request["params"]),
                    }
                )
            )
        upload = self.client.http.post(
            "files",
            data={"purpose": "batch"},
            files={"file": ("geo-audit.jsonl", "\n".join(lines) + "\n", "application/jsonl")},
        )
        upload.raise_for_status()
        response = self.client.http.post(
            "batches",
            json={
                "input_file_id": upload.json()["id"],
                "endpoint": "/v1/responses",
                "completion_window": "24h",
            },
        )
        response.raise_for_status()
        return SimpleNamespace(id=response.json()["id"])

    def retrieve(self, batch_id: str) -> Any:
        """Return pending or terminal status without exposing provider errors."""
        response = self.client.http.get("batches/" + batch_id)
        response.raise_for_status()
        raw = response.json()
        return SimpleNamespace(
            processing_status="ended"
            if raw["status"] in {"completed", "failed", "expired", "cancelled"}
            else raw["status"],
            raw=raw,
        )

    def results(self, batch_id: str) -> Iterator[MessageBatchIndividualResponse]:
        """Read both successful and failed rows; never resubmit missing results."""
        from .openai_client import normalize_response

        current = self.retrieve(batch_id).raw
        if current["status"] == "failed":
            raise RuntimeError(
                "OpenAI batch failed validation; inspect the provider before retrying"
            )
        for field in ("output_file_id", "error_file_id"):
            if not current.get(field):
                continue
            response = self.client.http.get("files/" + current[field] + "/content")
            response.raise_for_status()
            for line in response.text.splitlines():
                item = json.loads(line)
                result = item.get("response") or {}
                if result.get("status_code") == 200:
                    outcome = {"type": "succeeded", "message": normalize_response(result["body"])}
                else:
                    outcome = {
                        "type": "errored",
                        "error": {
                            "status_code": result.get("status_code"),
                            "code": (item.get("error") or {}).get("code"),
                        },
                    }
                yield MessageBatchIndividualResponse(
                    custom_id=item["custom_id"], result=BatchResult.model_validate(outcome)
                )
