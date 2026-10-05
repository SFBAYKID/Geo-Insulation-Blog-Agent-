"""Provider-independent saved text, tool calls and usage records."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Block(BaseModel):
    """A text, evidence or application-tool block in a saved response."""

    model_config = ConfigDict(extra="allow")
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    """OpenAI counters; input includes cached tokens, output includes reasoning."""

    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    web_search_requests: int = 0


class Message(BaseModel):
    """Normalized response retained for resumable writing and editorial checks."""

    model_config = ConfigDict(extra="allow")
    id: str
    model: str
    content: list[Block]
    stop_reason: str
    usage: Usage | None = None


class BatchResult(BaseModel):
    """One batch outcome, including terminal failures without resubmission."""

    type: str
    message: Message | None = None
    error: dict[str, Any] | None = None


class MessageBatchIndividualResponse(BaseModel):
    """Associate a normalized outcome with its original request."""

    custom_id: str
    result: BatchResult
