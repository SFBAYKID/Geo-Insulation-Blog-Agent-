"""Save a SerpApi key without echoing it or putting it in shell history."""

from __future__ import annotations

import getpass
import os
from pathlib import Path

p = Path(__file__).resolve().parents[1] / ".env"
key = getpass.getpass("Paste your SerpApi key (hidden), then press Enter: ").strip()
if not key or not key.isalnum():
    raise SystemExit("Key not saved: expected a nonempty alphanumeric key.")
lines = p.read_text().splitlines() if p.exists() else []
lines = [line for line in lines if not line.startswith("SERPAPI_API_KEY=")]
lines.append("SERPAPI_API_KEY=" + key)
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, "w") as f:
    f.write("\n".join(lines) + "\n")
print("Search key saved locally. You can tell Codex it is ready.")
