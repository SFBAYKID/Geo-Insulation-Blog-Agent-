"""Geo-specific integration boundaries exercised without external side effects."""

import json
from unittest.mock import Mock

import httpx
import pytest
from pydantic import ValidationError

from geo_blog.keyword_catalog import select_group
from geo_blog.media_catalog import attach_media
from geo_blog.requests import maybe_draft, process_request
from geo_blog.settings import Settings
from geo_blog.slack_guard import safe_client
from geo_blog.slack_text import article_chunks
from geo_blog.store import Store
from geo_blog.topics import select_topic


def test_production_channel_blocked_before_slack_io():
    settings = Settings(_env_file=None)
    raw = Mock()
    client = safe_client(settings, raw)
    for method in (
        client.chat_postMessage,
        client.chat_update,
        client.chat_postEphemeral,
    ):
        with pytest.raises(ValueError, match="test channel"):
            method(channel=settings.slack_production_channel_id, text="test")
    assert not raw.mock_calls
    with pytest.raises(ValidationError):
        Settings(_env_file=None, slack_channel_id=settings.slack_production_channel_id)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, publishing_enabled=True)


def test_group_notifications_and_broadcast_are_suppressed():
    settings = Settings(_env_file=None)
    raw = Mock()
    safe_client(settings, raw).chat_postMessage(
        channel=settings.slack_channel_id,
        thread_ts="100.1",
        text="<!here> <!channel|everyone>",
        reply_broadcast=True,
    )
    sent = raw.chat_postMessage.call_args.kwargs
    assert sent["reply_broadcast"] is False
    assert "<!" not in sent["text"]
    assert sent["unfurl_links"] is False


def test_keyword_groups_preserve_targets_and_supporting_rows():
    rows = [
        {
            "id": "recVariant",
            "fields": {
                "Keyword": "air leakage",
                "Topic": "Air leaks",
                "Target Page": "/services/blown-in insulation-repair",
                "Notes": "Supporting variant of: air sealing",
            },
        },
        {
            "id": "recPrimary",
            "fields": {
                "Keyword": "air sealing",
                "Topic": "Air leaks",
                "Target Page": "/services/blown-in insulation-repair",
            },
        },
        {
            "id": "recOther",
            "fields": {
                "Keyword": "air sealing",
                "Topic": "Air leaks",
                "Target Page": "(create page at /new)",
            },
        },
    ]
    selected = select_group(rows, set())
    assert selected["id"] == "recPrimary"
    assert selected["secondary_keyword"] == "air leakage"
    assert selected["source_record_ids"] == ["recVariant", "recPrimary"]
    assert "planned_url" not in selected
    assert select_group(rows, {"recPrimary"})["id"] == "recOther"


def test_actual_airtable_schema_respects_view_order():
    rows = [
        {"id": key, "fields": {"Keyword": key, "Topic": key, "Target Page": "/"}}
        for key in ["recA", "recB"]
    ]

    def handle(request):
        data = list(reversed(rows)) if "view" in request.url.params else rows
        return httpx.Response(200, json={"records": data})

    settings = Settings(
        _env_file=None,
        airtable_token="test",
        airtable_base_id="base",
        airtable_view="view",
    )
    topic = select_topic(settings, set(), httpx.Client(transport=httpx.MockTransport(handle)))
    assert topic["id"] == "recB"


def test_mention_request_deduplicates_and_ignores_production(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=tmp_path, slack_approver_ids="owner")
    store = Store(tmp_path)
    worker = Mock()
    monkeypatch.setattr("geo_blog.requests.WORKER", worker)
    event = {
        "user": "owner",
        "channel": settings.slack_channel_id,
        "ts": "100.1",
        "text": "<@bot> draft next",
    }
    assert not maybe_draft(
        settings,
        store,
        dict(event, channel=settings.slack_production_channel_id),
        Mock(),
        "bot",
    )
    assert not maybe_draft(settings, store, dict(event, user="stranger"), Mock(), "bot")
    assert maybe_draft(settings, store, event, Mock(), "bot")
    assert maybe_draft(settings, store, event, Mock(), "bot")
    worker.submit.assert_called_once()


def test_request_worker_binds_original_thread_and_records_result(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=tmp_path, slack_approver_ids="owner")
    store = Store(tmp_path)
    monkeypatch.setattr("geo_blog.requests.WORKER", Mock())
    event = {
        "user": "owner",
        "channel": settings.slack_channel_id,
        "ts": "101.1",
        "thread_ts": "100.1",
        "text": "<@bot> draft next",
    }
    raw = Mock()
    client = safe_client(settings, raw)
    maybe_draft(settings, store, event, client, "bot")
    monkeypatch.setattr(
        "geo_blog.topics.select_topic",
        Mock(return_value={"id": "recA", "keyword": "air sealing"}),
    )
    draft = Mock(return_value="draft123")
    monkeypatch.setattr("geo_blog.cli.run_daily", draft)
    key = settings.slack_channel_id + ":101.1"
    process_request(settings, store, key, client)
    process_request(settings, store, key, client)
    draft.assert_called_once()
    assert draft.call_args.kwargs["parent_thread"] == "100.1"
    assert raw.chat_postMessage.call_args.kwargs["thread_ts"] == "100.1"
    with store.db() as db:
        row = db.execute("SELECT * FROM draft_requests").fetchone()
    assert row["state"] == "complete" and row["draft_id"] == "draft123"


def test_catalog_never_uses_uncleared_or_mismatched_photo(tmp_path):
    settings = Settings(_env_file=None, image_catalog_path=tmp_path / "catalog.json")
    draft = {"topic": {"id": "recA"}, "markdown": "Text"}
    assert attach_media(settings, draft)["media_status"] == "awaiting_catalog"
    photo = {
        "id": "photo1",
        "drive_file_id": "drive1",
        "derivative_path": "assets/blog/missing.webp",
        "sha256": "0" * 64,
        "width": 1200,
        "height": 800,
        "factual_description": "A home door with a dent.",
        "keyword_record_ids": ["recA"],
        "publication_permission": "unknown",
        "privacy_review": "pending",
        "metadata_stripped": False,
    }
    settings.image_catalog_path.write_text(json.dumps({"schema_version": 1, "images": [photo]}))
    result = attach_media(settings, draft)
    assert result["media_status"] == "awaiting_approved_match"
    assert "hero" not in result["topic"]
    photo.update(
        publication_permission="approved",
        privacy_review="cleared",
        metadata_stripped=True,
        derivative_path="../private.webp",
    )
    settings.image_catalog_path.write_text(json.dumps({"schema_version": 1, "images": [photo]}))
    with pytest.raises(ValueError, match="inside assets/blog"):
        attach_media(settings, draft)


def test_article_chunking_preserves_all_words_and_bounds_messages():
    article = ("A useful repair explanation. " * 200) + "\n\n" + ("Another section. " * 200)
    chunks = article_chunks(article)
    assert all(len(chunk) <= 3000 for chunk in chunks)
    assert "".join("".join(chunks).split()) == "".join(article.split())


def test_draft_pipeline_delivers_only_inside_requested_thread(tmp_path, monkeypatch):
    """Exercise the real state transitions and Slack adapter with a fake writer/network."""
    from geo_blog.cli import run_daily

    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path,
        anthropic_api_key="test",
        slack_bot_token="test",
        slack_approver_ids="owner",
        image_catalog_path=tmp_path / "absent.json",
    )
    store = Store(tmp_path)
    topic = {"id": "recA", "keyword": "air sealing", "source_record_ids": ["recA", "recB"]}
    payload = {
        "topic": topic,
        "markdown": "# Air sealing\n\nA draft for review.",
        "front_matter": {"title": "Air sealing", "description": "A draft for review."},
        "report": {"word_count": 1000},
        "sources": [],
    }
    writer = Mock()

    def produce(topic, day, folder):
        folder.mkdir(parents=True, exist_ok=True)
        return payload

    writer.draft.side_effect = produce
    monkeypatch.setattr("geo_blog.content.Writer", lambda settings: writer)
    # Image fallback is covered in test_generated_hero; this test is about Slack threads.
    monkeypatch.setattr("geo_blog.generated_hero.ensure_image", lambda s, w, draft: draft)
    raw = Mock()
    raw.auth_test.return_value = {"team_id": settings.slack_team_id}
    raw.chat_postMessage.return_value = {"ts": "102.1"}
    monkeypatch.setattr("geo_blog.slack_guard.WebClient", lambda **kwargs: raw)
    draft_id = run_daily(
        settings,
        store,
        topic=topic,
        send=True,
        practice=True,
        parent_thread="100.1",
        request_key="request1",
    )
    assert store.required(draft_id)["status"] == "pending"
    assert {"recA", "recB"}.issubset(store.used_topics())
    for call in raw.chat_postMessage.call_args_list:
        assert call.kwargs["channel"] == settings.slack_test_channel_id
        assert call.kwargs["thread_ts"] == "100.1"
        assert call.kwargs["reply_broadcast"] is False
    assert "Photos are pending" in json.dumps(raw.chat_postMessage.call_args.kwargs["blocks"])
    raw.files_upload_v2.assert_not_called()


def test_owner_approved_share_only_allows_production_posts():
    """Production sharing cannot unlock edits, other channels or broadcasts."""
    from unittest.mock import Mock

    settings = Settings(
        _env_file=None,
        production_delivery_enabled=True,
        slack_production_channel_id="C_GEO_PRODUCTION_TEST",
    )
    raw = Mock()
    client = safe_client(settings, raw, production_share=True)
    client.chat_postMessage(
        channel=settings.slack_production_channel_id, text="Review", reply_broadcast=True
    )
    assert raw.chat_postMessage.call_args.kwargs["reply_broadcast"] is False
    with pytest.raises(ValueError):
        client.chat_update(channel=settings.slack_production_channel_id, ts="1", text="Edit")
    with pytest.raises(ValueError):
        client.chat_postMessage(channel="C_UNRELATED", text="Review")
    raw.chat_update.assert_not_called()
