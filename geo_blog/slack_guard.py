"""One outbound boundary for every Slack workflow, including resumed saved jobs."""

from __future__ import annotations

from typing import Any

from slack_sdk import WebClient

from .settings import Settings
from .slack_text import sentence_lines


def require_test_channel(settings: Settings, channel: str) -> None:
    """Fail before network I/O when an operation targets any other conversation."""
    if (
        settings.runtime_environment != "test"
        or not channel
        or channel != settings.slack_test_channel_id
        or channel != settings.slack_channel_id
        or channel == settings.slack_production_channel_id
    ):
        raise ValueError("Slack activity is restricted to the configured test channel")


class ChannelClient:
    """Expose only the Slack methods used by this application, with a channel check."""

    def __init__(
        self,
        settings: Settings,
        client: Any,
        *,
        production_share: bool = False,
        production_review_message_ts: str = "",
        production_thread: str = "",
    ) -> None:
        self.settings = settings
        self.production_thread = production_thread
        self.client = client
        self.production_review_message_ts = production_review_message_ts
        self.production_share = production_share

    def auth_test(self) -> Any:
        """Read identity without creating any visible Slack activity."""
        return self.client.auth_test()

    def _send(self, method: str, kwargs: dict[str, Any]) -> Any:
        # Explicit owner-approved review sharing can post to the production channel.
        # Production review updates additionally require the exact saved message ID.
        # Production chat may reply only inside its one named thread.
        # Normal test workflows never enable any explicit capability.
        if not (
            (
                (self.production_share and method == "chat_postMessage")
                or (
                    self.production_thread
                    and method == "chat_postMessage"
                    and kwargs.get("thread_ts") == self.production_thread
                )
                or (
                    self.production_review_message_ts
                    and method == "chat_update"
                    and kwargs.get("ts") == self.production_review_message_ts
                )
            )
            and self.settings.production_delivery_enabled
            and bool(self.settings.slack_production_channel_id)
            and kwargs.get("channel") == self.settings.slack_production_channel_id
        ):
            require_test_channel(self.settings, kwargs.get("channel", ""))
        # Suppress Slack-wide notifications even if user/model content contains them.
        import re

        def clean(value: Any) -> Any:
            if isinstance(value, str):
                return re.sub(
                    r"<!(?:channel|here|everyone)(?:\|[^>]+)?>",
                    "[group mention omitted]",
                    value,
                )
            if isinstance(value, list):
                return [clean(item) for item in value]
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items()}
            return value

        kwargs = clean(kwargs)
        # House style: one sentence per line in every message the agent sends.
        if isinstance(kwargs.get("text"), str):
            kwargs["text"] = sentence_lines(kwargs["text"])
        if isinstance(kwargs.get("blocks"), list):
            kwargs["blocks"] = _format_blocks(kwargs["blocks"])
        if method == "chat_postMessage":
            kwargs.update(reply_broadcast=False, unfurl_links=False, unfurl_media=False)
        return getattr(self.client, method)(**kwargs)

    def chat_postMessage(self, **kwargs: Any) -> Any:
        """Post only to the playground; never broadcast a threaded reply."""
        return self._send("chat_postMessage", kwargs)

    def chat_delete(self, **kwargs: Any) -> Any:
        """Remove an explicitly identified bot message only in the playground."""
        return self._send("chat_delete", kwargs)

    def chat_update(self, **kwargs: Any) -> Any:
        """Update only an explicitly identified message in the playground."""
        return self._send("chat_update", kwargs)

    def chat_postEphemeral(self, **kwargs: Any) -> Any:
        """Keep reviewer feedback within the same allowed channel."""
        return self._send("chat_postEphemeral", kwargs)


def _format_blocks(value: Any) -> Any:
    """Apply sentence lines to mrkdwn text only; buttons and headers stay as written."""
    if isinstance(value, list):
        return [_format_blocks(item) for item in value]
    if isinstance(value, dict):
        formatted = {key: _format_blocks(item) for key, item in value.items()}
        if value.get("type") == "mrkdwn" and isinstance(value.get("text"), str):
            formatted["text"] = sentence_lines(value["text"])
        return formatted
    return value


def safe_client(
    settings: Settings,
    client: Any = None,
    *,
    production_share: bool = False,
    production_review_message_ts: str = "",
    production_thread: str = "",
) -> ChannelClient:
    """Wrap injected clients too, so workers and tests exercise the same boundary."""
    if isinstance(client, ChannelClient):
        return client
    return ChannelClient(
        settings,
        client or WebClient(token=settings.slack_bot_token.get_secret_value()),
        production_share=production_share,
        production_review_message_ts=production_review_message_ts,
        production_thread=production_thread,
    )
