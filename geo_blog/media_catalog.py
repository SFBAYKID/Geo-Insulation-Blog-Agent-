"""Read approved photo derivatives; originals and descriptive cataloging stay external.

The image agent exports this small manifest. Relevance tags never become factual
alt text. Missing or unsuitable photos leave a reviewable text draft on hold for
media, rather than triggering paid generation or inventing customer evidence.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .settings import Settings


class Photo(BaseModel):
    """One manually cleared, privacy-reviewed derivative and its provenance."""

    model_config = ConfigDict(extra="forbid")
    id: str
    drive_file_id: str
    derivative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: int = Field(ge=640, le=4096)
    height: int = Field(ge=360, le=4096)
    factual_description: str = Field(min_length=1, max_length=350)
    caption: str = Field(default="", max_length=200)
    keyword_record_ids: list[str] = Field(default_factory=list)
    relevance_tags: list[str] = Field(default_factory=list)
    publication_permission: Literal["approved", "unknown", "denied"]
    privacy_review: Literal["cleared", "pending"]
    metadata_stripped: bool
    origin: Literal["real_photo", "generated"] = "real_photo"
    fallback_reason: str = ""


class Catalog(BaseModel):
    """Versioned interchange format; Airtable/Drive exports can evolve separately."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    images: list[Photo] = Field(default_factory=list)


def attach_media(settings: Settings, draft: dict[str, Any]) -> dict[str, Any]:
    """Attach an explicit record/task match only after provenance and file checks pass."""
    result = dict(draft, topic=dict(draft["topic"]))
    result["topic"].pop("hero", None)
    result.pop("media_provenance", None)
    result.pop("media_gallery", None)
    result["media_status"] = "awaiting_catalog"
    path = settings.image_catalog_path
    if not path.exists():
        return result
    catalog = Catalog.model_validate_json(path.read_text())
    record_ids = set(draft["topic"].get("source_record_ids", [draft["topic"]["id"]]))
    from .media_matches import candidate_image_ids

    image_ids = candidate_image_ids(draft["topic"], Path("config/blog-task-images.json"))
    root = Path("assets/blog").resolve()
    result["media_status"] = "awaiting_approved_match"
    from .media_usage import PhotoUsage

    slug = draft.get("front_matter", {}).get("slug")
    usage = PhotoUsage(settings.storage_dir) if slug else None
    selected: list[dict[str, Any]] = []
    for photo in sorted(
        catalog.images,
        key=lambda p: (
            p.origin != "real_photo",
            usage.rank(p.drive_file_id, slug) if usage else (0, 0),
        ),
    ):
        if (
            (not record_ids.intersection(photo.keyword_record_ids) and photo.id not in image_ids)
            or photo.publication_permission != "approved"
            or photo.privacy_review != "cleared"
            or not photo.metadata_stripped
            or (photo.origin == "generated" and not photo.fallback_reason.strip())
        ):
            continue
        file = Path(photo.derivative_path).resolve()
        if not file.is_relative_to(root) or file.suffix.lower() not in {".webp", ".jpg", ".jpeg"}:
            raise ValueError("Photo derivative must be a WebP or JPEG inside assets/blog")
        data = file.read_bytes()
        from .images import webp_size

        if (
            len(data) > 1_000_000
            or hashlib.sha256(data).hexdigest() != photo.sha256
            or (webp_size(data) if file.suffix.lower() == ".webp" else jpeg_size(data))
            != (photo.width, photo.height)
        ):
            raise ValueError("Photo derivative failed integrity, dimensions or size checks")
        if usage and not usage.reserve(photo.drive_file_id, slug):
            result["media_status"] = "awaiting_fresh_approved_match"
            continue
        selected.append(photo.model_dump())
        if len(selected) == 3:
            break
    if selected:
        photo_data = selected[0]
        result["topic"]["hero"] = {
            "src": "/blog/media/" + Path(photo_data["derivative_path"]).name,
            "alt": photo_data["factual_description"],
            "caption": photo_data["caption"],
            "width": photo_data["width"],
            "height": photo_data["height"],
        }
        result["media_status"] = "ready"
        result["media_provenance"] = photo_data
        result["media_gallery"] = selected[1:]
    return result


def jpeg_size(data: bytes) -> tuple[int, int]:
    """Read JPEG frame dimensions and reject unstripped identifying metadata."""
    import struct

    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Invalid JPEG")
    pos = 2
    dimensions = None
    while pos + 4 <= len(data):
        if data[pos] != 255:
            raise ValueError("Malformed JPEG marker")
        marker = data[pos + 1]
        if marker in {0xDA, 0xD9}:
            break
        size = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        if size < 2 or pos + 2 + size > len(data):
            raise ValueError("Truncated JPEG")
        if marker in {0xE1, 0xED, 0xFE}:
            raise ValueError("JPEG still contains private metadata")
        if marker in {0xC0, 0xC1, 0xC2}:
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            dimensions = (width, height)
        pos += 2 + size
    if dimensions is None:
        raise ValueError("JPEG has no supported frame")
    return dimensions
