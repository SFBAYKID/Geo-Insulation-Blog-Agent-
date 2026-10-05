import base64
import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from geo_blog.images import generate, webp_size
from geo_blog.settings import Settings

BRIEF = {
    "scene": "A bridge with a missing plank.",
    "alt": "Clay characters repair a bridge.",
    "caption": "Check before handing off.",
}
# Minimal extended header fixture for exercising the parser, not a display asset.
WEBP = (
    b"RIFF"
    + (22).to_bytes(4, "little")
    + b"WEBPVP8X"
    + (10).to_bytes(4, "little")
    + bytes(4)
    + (1535).to_bytes(3, "little")
    + (863).to_bytes(3, "little")
)


def test_generation_is_private_and_reuses_completed_image(tmp_path):
    s = Settings(
        _env_file=None,
        image_generation_enabled=True,
        image_generation_fallback_reason="No approved relevant photo in synthetic test",
        openai_api_key="test-private-key",
    )
    response = io.BytesIO(
        json.dumps({"data": [{"b64_json": base64.b64encode(WEBP).decode()}]}).encode()
    )
    call = Mock(return_value=response)
    hero = generate(s, BRIEF, tmp_path / "draft", assets=tmp_path / "assets", open_url=call)
    assert (hero["width"], hero["height"]) == (1536, 864)
    request = call.call_args.args[0]
    assert request.full_url == "https://api.openai.com/v1/images/generations"
    assert request.headers["Authorization"] == "Bearer test-private-key"
    assert json.loads(request.data)["model"] == "gpt-image-2.5-flare-2026-09-08"
    assert "test-private-key" not in (tmp_path / "draft/image-generation.json").read_text()
    assert generate(s, BRIEF, tmp_path / "draft", assets=tmp_path / "assets", open_url=call) == hero
    assert call.call_count == 1
    with pytest.raises(RuntimeError, match="brief changed"):
        generate(
            s,
            dict(BRIEF, scene="A different scene"),
            tmp_path / "draft",
            assets=tmp_path / "assets",
            open_url=call,
        )
    assert call.call_count == 1


def test_quota_error_does_not_trigger_paid_retry_or_leak_response(tmp_path):
    error = HTTPError(
        "url",
        429,
        "error",
        {},
        io.BytesIO(b'{"error":{"code":"insufficient_quota","message":"private-detail"}}'),
    )
    call = Mock(side_effect=error)
    sleep = Mock()
    with pytest.raises(RuntimeError, match="HTTP 429") as exc:
        generate(
            Settings(
                _env_file=None,
                image_generation_enabled=True,
                image_generation_fallback_reason="No approved relevant photo in synthetic test",
                openai_api_key="private-key",
            ),
            BRIEF,
            tmp_path / "draft",
            assets=tmp_path / "assets",
            open_url=call,
            sleep=sleep,
        )
    assert "private" not in str(exc.value)
    assert call.call_count == 1
    sleep.assert_not_called()


def test_uncertain_request_is_not_silently_repeated(tmp_path):
    s = Settings(
        _env_file=None,
        image_generation_enabled=True,
        image_generation_fallback_reason="No approved relevant photo in synthetic test",
        openai_api_key="test",
    )
    call = Mock(side_effect=URLError("private-debug"))
    with pytest.raises(RuntimeError, match="uncertain"):
        generate(s, BRIEF, tmp_path / "draft", assets=tmp_path / "assets", open_url=call)
    with pytest.raises(RuntimeError, match="Previous image generation needs review"):
        generate(s, BRIEF, tmp_path / "draft", assets=tmp_path / "assets", open_url=call)
    assert call.call_count == 1


def test_bad_images_and_dimensions_are_rejected():
    assert webp_size(WEBP) == (1536, 864)
    with pytest.raises(ValueError):
        webp_size(b"<html>login</html>")


def test_fresh_visual_pipeline_uses_api_and_never_silently_falls_back(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from anthropic.types import Message
    from test_visuals import exercise

    from geo_blog import images, visuals

    folder = tmp_path / "article"
    folder.mkdir()
    (folder / "company.json").write_text("[]")
    research = Message(
        id="test",
        type="message",
        role="assistant",
        model="test",
        stop_reason="end_turn",
        content=[{"type": "text", "text": "Verified brief"}],
        usage={"input_tokens": 1, "output_tokens": 1},
    )
    (folder / "research.json").write_text(research.model_dump_json())
    settings = Settings(
        _env_file=None,
        image_generation_enabled=True,
        image_generation_fallback_reason="No approved relevant photo in synthetic test",
        openai_api_key="test",
    )
    writer = Mock()
    writer.s = settings
    writer.call.return_value.model_dump_json.return_value = "{}"
    e = exercise()
    plan = {"hero": BRIEF, "exercise": e, "evidence_indices": [0]}
    monkeypatch.setattr(visuals, "response_json", Mock(return_value=plan))
    monkeypatch.setattr(
        visuals,
        "editorial_review",
        Mock(return_value={"verdict": "approve", "notes": []}),
    )
    monkeypatch.setattr(
        visuals,
        "validate",
        lambda *args: ({}, "", SimpleNamespace(passed=True, summary={})),
    )
    generated = {
        "src": "/blog/media/article-hero.webp",
        "alt": BRIEF["alt"],
        "caption": BRIEF["caption"],
        "width": 1536,
        "height": 864,
    }
    call = Mock(return_value=generated)
    monkeypatch.setattr(images, "generate", call)
    draft = {
        "markdown": "Intro\n\n## A section\n\nEvidence.",
        "topic": {"keyword": "example"},
        "sources": [],
    }
    result = visuals.add_visuals(writer, draft, folder)
    assert result["topic"]["hero"] == generated
    call.assert_called_once_with(settings, BRIEF, folder)
    assert json.loads((folder / "visual-plan.json").read_text())["art"] == "generated"
    assert "diagram" not in result["topic"]

    monkeypatch.setattr(visuals, "response_json", Mock(return_value=plan))
    call.side_effect = RuntimeError("Image service unavailable")
    with pytest.raises(RuntimeError, match="Image service unavailable"):
        visuals.add_visuals(
            writer,
            {
                "markdown": "Intro\n\n## A section\n\nEvidence.",
                "topic": {"keyword": "example"},
                "sources": [],
            },
            folder,
        )
