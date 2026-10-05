"""Exercise real preview QA under the Droplet's service restrictions, without sending a draft."""

from __future__ import annotations

import json
import platform
import socket
import subprocess
import time
from pathlib import Path

import httpx

from geo_blog.audit_config import cpu_args

root = Path.cwd()
folder = root / "storage/server-smoke"
info = json.loads((folder / "info.json").read_text())
(folder / "passed.json").unlink(missing_ok=True)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
url = f"http://127.0.0.1:{port}" + info["path"]
with (folder / "server.log").open("a") as log:
    server = subprocess.Popen(
        [
            "node",
            "node_modules/next/dist/bin/next",
            "start",
            "--hostname",
            "127.0.0.1",
            "-p",
            str(port),
        ],
        cwd=folder / "website",
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(30):
            try:
                if httpx.get(url).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Preview failed to start")
        subprocess.run(
            ["node", "tools/check_preview.mjs", url, info["product"], str(folder)],
            check=True,
            timeout=180,
        )
        subprocess.run(
            [
                str(root / "node_modules/.bin/lighthouse"),
                url,
                "--chrome-flags=--headless=new",
                *cpu_args(),
                "--only-categories=performance,accessibility,best-practices,seo",
                "--output=json",
                "--output-path=" + str(folder / "lighthouse.json"),
                "--quiet",
            ],
            check=True,
            timeout=180,
        )
        j = json.loads((folder / "lighthouse.json").read_text())
        scores = {k: round(v["score"] * 100) for k, v in j["categories"].items()}
        print("Raw server mobile Lighthouse:", scores, flush=True)
        assert (
            scores["performance"] >= 90
            and scores["accessibility"] >= 95
            and scores["best-practices"] >= 95
        )
        (folder / "passed.json").write_text(
            json.dumps({"scores": scores, "checked_at": time.time(), "host": platform.node()})
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
