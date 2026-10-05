"""Geo setup cannot inherit production authority or buy disabled imagery."""

from unittest.mock import Mock

import pytest

from geo_blog.evidence import catalog_packet
from geo_blog.generated_hero import ensure_image
from geo_blog.production_publish import comments_digest, run_once, validate_approval
from geo_blog.settings import Settings
from geo_blog.slack_guard import safe_client
from geo_blog.store import Store
from geo_blog.weekly import run_weekly


def test_disabled_production_blocks_explicit_share_before_slack_io():
    settings = Settings(_env_file=None, slack_production_channel_id="C_GEO_PROD")
    raw = Mock()
    with pytest.raises(ValueError, match="test channel"):
        safe_client(settings, raw, production_share=True).chat_postMessage(
            channel="C_GEO_PROD", text="Must not send"
        )
    raw.chat_postMessage.assert_not_called()


def test_setup_does_not_draft_or_resume_production(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=tmp_path)
    draft = Mock()
    monkeypatch.setattr("geo_blog.cli.run_daily", draft)
    with pytest.raises(ValueError, match="PRODUCTION_DELIVERY_ENABLED"):
        run_weekly(settings, Store(tmp_path))
    assert run_once(settings) is False
    draft.assert_not_called()


def test_saved_approval_loses_authority_when_reviewer_removed():
    review = {"state": "queued", "draft_id": "one", "preview_commit": "sha", "reviewer": "U_OLD"}
    review["approved_comments_digest"] = comments_digest(review)
    plan = {"armed": True, "draft_id": "one", "head_sha": "sha"}
    validate_approval(review, plan, {"U_OLD"})
    with pytest.raises(ValueError, match="exact-version approval"):
        validate_approval(review, plan, {"U_NEW"})
    with pytest.raises(ValueError, match="exact-version approval"):
        validate_approval(review, plan)


def test_disabled_image_generation_stops_before_paid_writer_call():
    writer = Mock()
    with pytest.raises(ValueError, match="IMAGE_GENERATION_ENABLED"):
        ensure_image(Settings(_env_file=None), writer, {"media_status": "awaiting_catalog"})
    writer.call.assert_not_called()


def test_actual_geo_service_routes_are_included_in_verified_catalog():
    pages = [
        {"url": "https://geo-insulation.com/fiberglass-insulation/", "text": "Verified page"},
        {"url": "https://geo-insulation.com/blog/example/", "text": "Blog"},
        {"url": "https://geo-insulation.com/nonexistent-service/", "text": "Proposed"},
    ]
    assert catalog_packet(pages) == [pages[0]]
