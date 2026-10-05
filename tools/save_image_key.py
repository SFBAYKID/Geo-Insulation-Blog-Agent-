"""Save the OpenAI image API key privately, without shell history or echoed input."""

from __future__ import annotations

import getpass
import os
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
key = getpass.getpass("Paste your OpenAI API key (hidden), then press Enter: ").strip()
if (
    not key.startswith("sk-")
    or any(c.isspace() for c in key)
    or not all(c.isalnum() or c in "-_" for c in key)
):
    raise SystemExit("Key not saved: expected an OpenAI API key starting with sk-.")
lines = target.read_text().splitlines() if target.exists() else []
lines = [
    line
    for line in lines
    if not line.strip().startswith(("OPENAI_API_KEY=", "export OPENAI_API_KEY="))
]
lines.append("OPENAI_API_KEY=" + key)
fd, temporary = tempfile.mkstemp(prefix=".env-save-", dir=root)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("\n".join(lines) + "\n")
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print(
    'OpenAI key saved privately in this project. Reply "saved" so Codex can connect and test it on the Droplet.'
)
