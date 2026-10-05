"""Check derivative integrity and approval gates without external calls or secrets."""

import hashlib
import json

import pytest
from PIL import Image

from geo_blog.media_catalog import attach_media
from geo_blog.settings import Settings
from tools.prepare_catalog_photos import prepare


def fixture_catalog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "source"
    (root / "originals").mkdir(parents=True)
    (root / "analysis").mkdir()
    image = Image.new("RGB", (1800, 1400), "silver")
    exif = Image.Exif()
    exif[270] = "Private metadata must not survive"
    source = root / "originals/photo1.jpg"
    image.save(source, exif=exif)
    (root / "analysis/photo1.json").write_text(
        json.dumps({"source_md5": hashlib.md5(source.read_bytes()).hexdigest()})
    )
    (root / "keywords.json").write_text(
        json.dumps([{"id": "rec1", "fields": {"Topic": "Attic Insulation"}}])
    )
    decisions = {
        "publication_permission": "unknown",
        "photos": [
            {
                "drive_file_id": "photo1",
                "privacy_review": "cleared",
                "approved_topics": ["Attic Insulation"],
                "factual_description": "A silver test panel.",
                "crop": None,
            }
        ],
    }
    (root / "review-decisions.json").write_text(json.dumps(decisions))
    return root, decisions


def test_prepare_metadata_integrity_and_permission_gate(tmp_path, monkeypatch):
    root, decisions = fixture_catalog(tmp_path, monkeypatch)
    manifest = tmp_path / "catalog.json"
    assert prepare(root, manifest) == 1
    settings = Settings(_env_file=None, image_catalog_path=manifest)
    draft = {"topic": {"id": "rec1", "source_record_ids": ["rec1"]}}
    assert attach_media(settings, draft)["media_status"] == "awaiting_approved_match"
    decisions["publication_permission"] = "approved"
    (root / "review-decisions.json").write_text(json.dumps(decisions))
    assert prepare(root, manifest) == 1  # A repeated export does not duplicate entries.
    attached = attach_media(settings, draft)
    assert attached["media_status"] == "ready"
    derivative = attached["media_provenance"]["derivative_path"]
    with Image.open(derivative) as image:
        assert not image.getexif() and "exif" not in image.info
        assert image.width <= 1600 and image.height <= 1200
    assert attach_media(settings, {"topic": {"id": "unrelated"}})["media_status"] != "ready"


def test_changed_source_cannot_reuse_review(tmp_path, monkeypatch):
    root, _ = fixture_catalog(tmp_path, monkeypatch)
    (root / "originals/photo1.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Reviewed source changed"):
        prepare(root, tmp_path / "catalog.json")


def test_selected_original_is_fetched_when_cache_is_empty(tmp_path, monkeypatch):
    root, _ = fixture_catalog(tmp_path, monkeypatch)
    source = root / "originals/photo1.jpg"
    data = source.read_bytes()
    source.unlink()
    calls = []

    def download(file_id, checksum, destination):
        calls.append(file_id)
        assert hashlib.md5(data).hexdigest() == checksum
        destination.write_bytes(data)

    monkeypatch.setattr("tools.prepare_catalog_photos.download_source", download)
    assert prepare(root, tmp_path / "catalog.json") == 1
    assert calls == ["photo1"]
