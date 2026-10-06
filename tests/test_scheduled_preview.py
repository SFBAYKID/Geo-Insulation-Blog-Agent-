"""Scheduled drafts stay in the playground and run once across Pacific DST."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from geo_blog.scheduled_preview import readiness, run
from geo_blog.settings import Settings


@pytest.mark.parametrize("stamp", ["2026-10-07T16:00:00+00:00", "2026-11-04T17:00:00+00:00"])
def test_pacific_nine_am_is_due_in_summer_and_winter(tmp_path, stamp):
    s = Settings(_env_file=None, storage_dir=tmp_path, blog_queue_source="basecamp")
    result = run(s, now=datetime.fromisoformat(stamp))
    assert result["status"] == "not_ready"
    assert "basecamp_refresh_token" in result["missing"]
    assert run(s, now=datetime.fromisoformat(stamp))["status"] == "already_attempted"


@pytest.mark.parametrize(
    "stamp", ["2026-10-07T17:00:00+00:00", "2026-11-04T16:00:00+00:00", "2026-10-08T16:00:00+00:00"]
)
def test_other_cron_slot_and_wrong_day_do_nothing(tmp_path, stamp):
    assert run(
        Settings(_env_file=None, storage_dir=tmp_path), now=datetime.fromisoformat(stamp)
    ) == {"status": "outside_schedule"}
    assert not (tmp_path / "scheduled-previews").exists()


def test_production_delivery_is_rejected():
    s = Settings(_env_file=None, production_delivery_enabled=True)
    with pytest.raises(ValueError, match="production delivery disabled"):
        readiness(s)


def test_ambiguous_generation_is_not_retried(tmp_path, monkeypatch):
    s = Settings(_env_file=None, storage_dir=tmp_path)
    monkeypatch.setattr("geo_blog.scheduled_preview.readiness", lambda _: [])
    worker = Mock(side_effect=TimeoutError("do not leak this detail"))
    monkeypatch.setattr("geo_blog.cli.run_daily", worker)
    now = datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)
    assert run(s, now=now) == {"status": "needs_inspection", "error": "TimeoutError"}
    assert run(s, now=now)["status"] == "already_attempted"
    assert worker.call_count == 1
    assert worker.call_args.kwargs["send"] is True


def test_production_schedule_requires_publishing_and_destination(tmp_path):
    s = Settings(_env_file=None, storage_dir=tmp_path)
    missing = readiness(s, production=True)
    assert "publishing_enabled" in missing
    assert "production_delivery_enabled" in missing
    assert "slack_production_channel_id" in missing


def test_production_schedule_uses_guarded_weekly_flow_once(tmp_path, monkeypatch):
    s = Settings(_env_file=None, storage_dir=tmp_path)
    monkeypatch.setattr("geo_blog.scheduled_preview.readiness", lambda *a, **kw: [])
    worker = Mock(return_value="new-blog")
    monkeypatch.setattr("geo_blog.weekly.run_weekly_notifying", worker)
    now = datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)
    assert run(s, now=now, production=True)["draft_id"] == "new-blog"
    assert run(s, now=now, production=True)["status"] == "already_attempted"
    assert worker.call_count == 1
    assert (tmp_path / "scheduled-production" / "2026-10-07.json").exists()
