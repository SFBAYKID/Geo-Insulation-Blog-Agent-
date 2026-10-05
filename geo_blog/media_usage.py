"""Reserve original photos by article, preventing near-term reuse across new blogs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

COOLDOWN_DAYS = 90


class PhotoUsage:
    """Persistent source-ID history; rechecking the same article never consumes another use."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "photo-usage.sqlite3"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS uses(source TEXT, article TEXT, used_at REAL, PRIMARY KEY(source,article))"
            )
            db.execute("CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY)")
            # Existing reviewed drafts predate this ledger. Conservatively reserve
            # their originals today rather than guessing when photos were selected.
            if not db.execute("SELECT 1 FROM migrations WHERE name='existing-drafts'").fetchone():
                source = root / "blog.sqlite3"
                if source.exists():
                    with closing(sqlite3.connect(source)) as old:
                        for (payload,) in old.execute(
                            "SELECT payload FROM drafts WHERE payload IS NOT NULL"
                        ):
                            draft = json.loads(payload)
                            slug = draft.get("front_matter", {}).get("slug")
                            photos = [
                                draft.get("media_provenance", {}),
                                *draft.get("media_gallery", []),
                            ]
                            for photo in photos:
                                if slug and photo.get("drive_file_id"):
                                    db.execute(
                                        "INSERT OR IGNORE INTO uses VALUES(?,?,?)",
                                        (
                                            photo["drive_file_id"],
                                            slug,
                                            datetime.now(timezone.utc).timestamp(),
                                        ),
                                    )
                db.execute("INSERT INTO migrations VALUES('existing-drafts')")

    def rank(self, source: str, article: str) -> tuple[int, float]:
        """Keep a draft's photo stable; otherwise prefer unused originals, then oldest."""
        with closing(sqlite3.connect(self.path)) as db:
            if db.execute(
                "SELECT 1 FROM uses WHERE source=? AND article=?", (source, article)
            ).fetchone():
                return (-1, 0)
            latest = db.execute(
                "SELECT MAX(used_at) FROM uses WHERE source=?", (source,)
            ).fetchone()[0]
            return (0, 0) if latest is None else (1, latest)

    def reserve(self, source: str, article: str, *, now: float | None = None) -> bool:
        """Atomically reject another article's recent use, including another crop of it."""
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM uses WHERE source=? AND article=?", (source, article)
            ).fetchone():
                return True
            recent = db.execute(
                "SELECT 1 FROM uses WHERE source=? AND used_at>?",
                (source, now - COOLDOWN_DAYS * 86400),
            ).fetchone()
            if recent:
                return False
            db.execute("INSERT INTO uses VALUES(?,?,?)", (source, article, now))
            return True
