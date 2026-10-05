"""Prepare explicitly reviewed real photos for the blog's existing manifest contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from PIL import Image, ImageOps

from geo_blog.media_catalog import Catalog, Photo
from tools.keyword_sync import save


def download_source(file_id: str, checksum: str, destination: Path) -> None:
    """Fetch a selected original on demand and verify its reviewed source version."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    credential_path = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS", str(Path.home() / ".config/geo/google-drive-sa.json")
    )
    credentials = service_account.Credentials.from_service_account_file(
        credential_path, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    session = AuthorizedSession(credentials)
    response = session.get(
        "https://www.googleapis.com/drive/v3/files/" + file_id,
        params={"supportsAllDrives": "true", "alt": "media"},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Selected source unavailable: HTTP {response.status_code}")
    if hashlib.md5(response.content).hexdigest() != checksum:
        raise ValueError("Reviewed source changed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    destination.chmod(0o600)


def prepare(root: Path, manifest: Path) -> int:
    """Export only reviewed selections; preserve other producers' manifest entries."""
    decisions = json.loads((root / "review-decisions.json").read_text())
    records = json.loads((root / "keywords.json").read_text())
    existing = Catalog.model_validate_json(manifest.read_text()) if manifest.exists() else Catalog()
    photos = {p.id: p for p in existing.images}
    for decision in decisions["photos"]:
        if decision["privacy_review"] != "cleared":
            continue
        fid = decision["drive_file_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", fid):
            raise ValueError("Invalid Drive identity")
        source = root / "originals" / (fid + ".jpg")
        analysis = json.loads((root / "analysis" / (fid + ".json")).read_text())
        if not source.exists():
            download_source(fid, analysis["source_md5"], source)
        if hashlib.md5(source.read_bytes()).hexdigest() != analysis["source_md5"]:
            raise ValueError("Reviewed source changed")
        # Fresh pixel storage and no EXIF/ICC arguments ensure metadata is not copied.
        with Image.open(source) as original:
            oriented = ImageOps.exif_transpose(original).convert("RGB")
            if decision.get("crop"):
                oriented = oriented.crop(tuple(decision["crop"]))
            oriented.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
            clean = Image.new("RGB", oriented.size)
            clean.paste(oriented)
        if clean.width < 640 or clean.height < 360:
            raise ValueError("Reviewed image is too small for the manifest")
        folder = Path("assets/blog")
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / ("drive-" + fid + ".pending.webp")
        clean.save(temporary, "WEBP", quality=85, method=6)
        data = temporary.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        destination = folder / ("drive-" + fid + "-" + digest[:12] + ".webp")
        temporary.replace(destination)
        with Image.open(destination) as verified:
            if verified.getexif() or any(
                k in verified.info for k in ("exif", "xmp", "icc_profile")
            ):
                raise ValueError("Derivative still contains metadata")
        if len(data) > 1_000_000:
            raise ValueError("Derivative exceeds the blog size limit")
        topics = set(decision["approved_topics"])
        ids = [r["id"] for r in records if r["fields"].get("Topic") in topics]
        if not ids:
            raise ValueError("Reviewed image has no matching keyword records")
        photo = Photo(
            id="drive-" + fid,
            drive_file_id=fid,
            derivative_path=str(destination),
            sha256=digest,
            width=clean.width,
            height=clean.height,
            factual_description=decision["factual_description"],
            keyword_record_ids=ids,
            relevance_tags=sorted(topics),
            publication_permission=decisions["publication_permission"],
            privacy_review="cleared",
            metadata_stripped=True,
        )
        photos[photo.id] = photo
    output = Catalog(images=list(photos.values()))
    save(manifest, output.model_dump())
    return len(output.images)


def main() -> None:
    """Build the handoff from local review decisions, without generating any imagery."""
    total = prepare(Path("storage/photo-analysis"), Path("config/image-catalog.json"))
    print(f"Manifest contains {total} reviewed derivatives; inspect final pixels before release.")


if __name__ == "__main__":
    main()
