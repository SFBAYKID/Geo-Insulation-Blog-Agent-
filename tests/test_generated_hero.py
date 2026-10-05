"""A blog never ships without an image; generated fallbacks are labeled illustrations."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from geo_blog import generated_hero
from geo_blog.settings import Settings

DRAFT = {
    "front_matter": {
        "title": "How Does Blown-In Insulation Work?",
        "slug": "blown-in-insulation",
        "description": "D",
    },
    "topic": {"id": "basecamp:1", "keyword": "how does blown-in insulation work"},
    "media_status": "awaiting_approved_match",
}


def writer_returning(brief):
    writer = Mock()
    writer.call.return_value = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(brief))],
    )
    return writer


def test_real_photo_is_kept_and_nothing_is_generated(tmp_path, monkeypatch):
    monkeypatch.setattr(generated_hero, "generate", Mock())
    ready = dict(DRAFT, media_status="ready", media_provenance={"sha256": "x"})
    writer = Mock()
    assert generated_hero.ensure_image(Settings(_env_file=None), writer, ready) is ready
    writer.call.assert_not_called()
    generated_hero.generate.assert_not_called()


def test_missing_photo_generates_a_labeled_illustration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "assets/blog").mkdir(parents=True)
    (tmp_path / "assets/blog/blown-in-insulation-hero.webp").write_bytes(b"image")
    seen = {}

    def fake_generate(settings, brief, folder, *, assets):
        seen.update(
            enabled=settings.image_generation_enabled,
            reason=settings.image_generation_fallback_reason,
            brief=brief,
        )
        return {
            "src": "/blog/media/blown-in-insulation-hero.webp",
            "width": 1536,
            "height": 864,
            "alt": brief["alt"],
            "caption": brief["caption"],
        }

    monkeypatch.setattr(generated_hero, "generate", fake_generate)
    writer = writer_returning(
        {
            "scene": "Insulation between attic framing.",
            "alt": "Loose-fill insulation between attic joists.",
            "caption": "Blown-in insulation in an attic.",
        }
    )
    result = generated_hero.ensure_image(
        Settings(
            _env_file=None,
            storage_dir=tmp_path,
            image_generation_enabled=True,
            openai_api_key="fake-test-key",
        ),
        writer,
        DRAFT,
    )
    assert result["media_status"] == "ready"
    assert result["media_provenance"]["origin"] == "generated"
    assert result["media_provenance"]["sha256"] == hashlib.sha256(b"image").hexdigest()
    assert result["topic"]["hero"]["caption"].startswith("Illustration: ")
    assert seen["enabled"] and "No privacy-cleared" in seen["reason"]


def test_generation_failure_leaves_no_image_so_the_blog_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(generated_hero, "generate", Mock(side_effect=RuntimeError("quota")))
    writer = writer_returning({"scene": "s", "alt": "a", "caption": "c"})
    with pytest.raises(RuntimeError):
        generated_hero.ensure_image(
            Settings(
                _env_file=None,
                storage_dir=tmp_path,
                image_generation_enabled=True,
                openai_api_key="fake-test-key",
            ),
            writer,
            DRAFT,
        )


def test_reference_descriptions_prefer_related_library_photos(tmp_path):
    folder = tmp_path / "drive-catalog"
    folder.mkdir()
    images = [
        {"analysis": {"factual_description": "Blown-in insulation across attic framing."}},
        {"analysis": {"factual_description": "A garden path."}},
    ]
    (folder / "catalog.json").write_text(json.dumps({"images": images}))
    refs = generated_hero.reference_descriptions(
        Settings(
            _env_file=None,
            storage_dir=tmp_path,
            image_generation_enabled=True,
            openai_api_key="fake-test-key",
        ),
        "blown-in insulation",
    )
    assert refs == ["Blown-in insulation across attic framing."]
