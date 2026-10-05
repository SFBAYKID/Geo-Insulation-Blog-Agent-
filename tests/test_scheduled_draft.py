"""Schedule timing, isolation and retry protection without network or paid calls."""

from datetime import datetime
from unittest.mock import patch

import pytest

from geo_blog.scheduled_draft import tick
from geo_blog.settings import Settings
from geo_blog.store import Store


@pytest.fixture
def setup(tmp_path):
    settings = Settings(_env_file=None, storage_dir=tmp_path)
    plan = {
        "id": "one",
        "enabled": True,
        "run_at": "2026-09-25T10:00:00-07:00",
        "channel": settings.slack_test_channel_id,
    }
    return settings, Store(tmp_path), plan


def test_due_once_and_no_early_send(setup):
    settings, store, plan = setup
    with patch("geo_blog.cli.run_daily", return_value="draft-one") as run:
        assert (
            tick(settings, store, plan, datetime.fromisoformat("2026-09-25T16:59:00+00:00"))
            == "waiting"
        )
        assert (
            tick(settings, store, plan, datetime.fromisoformat("2026-09-25T17:00:00+00:00"))
            == "complete"
        )
        assert (
            tick(settings, store, plan, datetime.fromisoformat("2026-09-25T17:01:00+00:00"))
            == "already_claimed"
        )
        assert run.call_count == 1
        assert run.call_args.kwargs["send"] is True


def test_failure_never_replays(setup):
    settings, store, plan = setup
    now = datetime.fromisoformat(plan["run_at"])
    with patch("geo_blog.cli.run_daily", side_effect=RuntimeError("secret")) as run:
        assert tick(settings, store, plan, now) == "failed"
        assert tick(settings, store, plan, now) == "already_claimed"
        assert run.call_count == 1
    with store.db() as db:
        assert db.execute("SELECT error FROM scheduled_drafts").fetchone()[0] == "RuntimeError"


def test_expired_and_production_rejected(setup):
    settings, store, plan = setup
    with patch("geo_blog.cli.run_daily") as run:
        assert (
            tick(settings, store, plan, datetime.fromisoformat("2026-09-26T10:00:00-07:00"))
            == "expired"
        )
        run.assert_not_called()
    plan["channel"] = settings.slack_production_channel_id
    with pytest.raises(ValueError):
        tick(settings, store, plan, datetime.fromisoformat(plan["run_at"]))


def test_published_variant_skips_entire_group():
    from geo_blog.keyword_catalog import select_group

    records = [
        {
            "id": "rec1",
            "fields": {"Keyword": "air sealing", "Topic": "Air leaks", "Target Page": "/air-leaks"},
        },
        {
            "id": "rec2",
            "fields": {
                "Keyword": "air leakage",
                "Topic": "Air leaks",
                "Target Page": "/air-leaks",
                "Publication Status": "Published",
            },
        },
        {
            "id": "rec3",
            "fields": {"Keyword": "paint repair", "Topic": "Paint", "Target Page": "/paint"},
        },
    ]
    assert select_group(records, set())["id"] == "rec3"
