"""Task-photo matching cannot silently drift or bypass catalog review."""

import json

import pytest

from geo_blog.media_matches import candidate_image_ids


def test_task_photo_match_requires_exact_identity_keyword_and_url(tmp_path):
    path = tmp_path / "matches.json"
    task = {
        "task_id": 42,
        "keyword": "old insulation",
        "planned_url": "/blog/insulation",
        "image_ids": ["photo-one"],
    }
    path.write_text(json.dumps({"schema_version": 1, "tasks": [task]}))
    topic = {"basecamp_task_id": 42, "keyword": task["keyword"], "planned_url": task["planned_url"]}
    assert candidate_image_ids(topic, path) == {"photo-one"}
    assert candidate_image_ids(dict(topic, basecamp_task_id=43), path) == set()
    assert candidate_image_ids({"id": "rec42"}, path) == set()
    for change in [{"keyword": "different"}, {"planned_url": "/blog/different"}]:
        with pytest.raises(ValueError, match="relevance changed"):
            candidate_image_ids(dict(topic, **change), path)
    path.write_text(json.dumps({"schema_version": 1, "tasks": [task, task]}))
    with pytest.raises(ValueError, match="Duplicate"):
        candidate_image_ids(topic, path)


def test_task_mapping_does_not_bypass_photo_approval_or_integrity(tmp_path, monkeypatch):
    from geo_blog.media_catalog import attach_media
    from geo_blog.settings import Settings

    monkeypatch.setattr("geo_blog.media_matches.candidate_image_ids", lambda *_: {"photo1"})
    settings = Settings(_env_file=None, image_catalog_path=tmp_path / "catalog.json")
    draft = {"topic": {"id": "basecamp:42", "source_record_ids": []}}
    photo = {
        "id": "photo1",
        "drive_file_id": "drive1",
        "derivative_path": "../private.webp",
        "sha256": "0" * 64,
        "width": 1200,
        "height": 800,
        "factual_description": "A old insulation.",
        "keyword_record_ids": [],
        "publication_permission": "unknown",
        "privacy_review": "cleared",
        "metadata_stripped": True,
    }
    settings.image_catalog_path.write_text(json.dumps({"schema_version": 1, "images": [photo]}))
    assert attach_media(settings, draft)["media_status"] == "awaiting_approved_match"
    photo["publication_permission"] = "approved"
    settings.image_catalog_path.write_text(json.dumps({"schema_version": 1, "images": [photo]}))
    with pytest.raises(ValueError, match="inside assets/blog"):
        attach_media(settings, draft)
