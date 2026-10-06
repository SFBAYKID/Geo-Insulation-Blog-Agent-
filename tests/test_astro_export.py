"""Real Astro data exports preserve existing posts and reject unsafe media."""

import hashlib
import json
from pathlib import Path

import pytest

from geo_blog.site_export import export_post


@pytest.fixture
def astro_draft(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = Path("assets/hero.webp")
    source.parent.mkdir()
    source.write_bytes(
        b"RIFF"
        + (22).to_bytes(4, "little")
        + b"WEBPVP8X"
        + (10).to_bytes(4, "little")
        + bytes(4)
        + (1535).to_bytes(3, "little")
        + (863).to_bytes(3, "little")
    )
    registry = Path("website/src/data/posts.js")
    registry.parent.mkdir(parents=True)
    registry.write_text(
        'export const posts = [\n{slug: "existing", body: "untouched"}\n];\nexport default posts;\n'
    )
    description = "Review attic insulation with a professional to understand existing materials, air leaks and ventilation before planning improvements for your home."
    draft = {
        "markdown": "---\n"
        + "\n".join(
            [
                "title: Attic Insulation Planning",
                "slug: attic-planning",
                "description: " + description,
                "keywords: [attic insulation]",
            ]
        )
        + "\n---\n# Attic Planning\n\nConsider [insulation](https://example.com/).\n\n## Frequently Asked Questions\n\n**What happens first?**\n\nDiscuss the existing conditions.\n",
        "topic": {
            "hero": {
                "src": "/blog/media/attic-planning-hero.webp",
                "alt": "An attic",
                "caption": "Illustration: An attic.",
                "width": 1536,
                "height": 864,
            }
        },
        "media_status": "ready",
        "media_provenance": {
            "origin": "generated",
            "derivative_path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
    }
    return draft, registry


def test_export_preserves_registry_and_refuses_duplicate(astro_draft):
    draft, registry = astro_draft
    export_post(draft, Path("website"), "2026-10-05")
    text = registry.read_text()
    assert '{slug: "existing", body: "untouched"}' in text
    post = json.JSONDecoder().raw_decode(text.split("[\n", 1)[1])[0]
    assert post["faqs"] == [
        {"question": "What happens first?", "answer": "Discuss the existing conditions."}
    ]
    assert "Frequently Asked Questions" not in post["body"]
    assert post["imageCaption"].startswith("Illustration: ")
    with pytest.raises(ValueError, match="existing article"):
        export_post(draft, Path("website"), "2026-10-05")


@pytest.mark.parametrize("change", ["hash", "path", "caption", "pending"])
def test_invalid_media_leaves_website_untouched(astro_draft, change):
    draft, registry = astro_draft
    original = registry.read_bytes()
    if change == "hash":
        draft["media_provenance"]["sha256"] = "bad"
    if change == "path":
        draft["topic"]["hero"]["src"] = "/../../outside.webp"
    if change == "caption":
        draft["topic"]["hero"]["caption"] = "Real customer project"
    if change == "pending":
        draft["media_status"] = "pending"
    with pytest.raises(ValueError):
        export_post(draft, Path("website"), "2026-10-05")
    assert registry.read_bytes() == original
    assert not Path("website/public").exists()
