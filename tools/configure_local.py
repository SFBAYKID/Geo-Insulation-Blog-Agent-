"""Temporary localhost-only credential entry. Stop with Ctrl-C when finished."""

from __future__ import annotations

import html
import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from dotenv import set_key

ENV = Path(__file__).resolve().parents[1] / ".env.local"
ROUTE = "/" + secrets.token_urlsafe(32)
FIELDS = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_APPROVER_IDS",
    "SLACK_SIGNING_SECRET",
    "SLACK_APP_ID",
    "SLACK_TEAM_ID",
    "SLACK_CLIENT_ID",
    "SLACK_BOT_USER_ID",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AIRTABLE_TOKEN",
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: dict[str, Any]) -> None:
        """Suppress HTTP access logging so the private setup route is not recorded."""
        pass

    def reply(self, status: Any, text: str) -> None:
        """Return an uncached setup page with restrictive browser security headers."""
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> Any:
        """Serve the private form only for the expected localhost host and random route."""
        if self.path != ROUTE or self.headers.get("Host") != self.server.expected_host:
            return self.reply(404, "Not found")
        inputs = "".join(
            f'<p><label>{name}<br><input name="{name}" type="password" autocomplete="off" style="width:100%"></label></p>'
            for name in FIELDS
        )
        self.reply(
            200,
            '<!doctype html><title>Geo Insulation Blog Agent setup</title><main style="max-width:650px;margin:50px auto;font-family:system-ui"><h1>Geo Insulation Blog Agent setup</h1><p>Saved only in this project’s local .env.local file. Leave fields blank to keep existing values. Enter Slack member IDs separated by commas.</p>'
            + f'<form method="post" action="{html.escape(ROUTE)}">{inputs}<button>Save locally</button></form></main>',
        )

    def do_POST(self) -> Any:
        """Validate local origin and bounded fields before updating private credentials."""
        if (
            self.path != ROUTE
            or self.headers.get("Host") != self.server.expected_host
            or self.headers.get("Origin") != "http://" + self.server.expected_host
        ):
            return self.reply(403, "Forbidden")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length < 16384:
            return self.reply(400, "Invalid request")
        fields = parse_qs(self.rfile.read(length).decode())
        values = {name: fields.get(name, [""])[0].strip() for name in FIELDS}
        if any("\n" in v or "\r" in v for v in values.values()):
            return self.reply(400, "Values must use one line")
        for name, prefix in (
            ("SLACK_BOT_TOKEN", "xoxb-"),
            ("SLACK_APP_TOKEN", "xapp-"),
        ):
            if values[name] and not values[name].startswith(prefix):
                return self.reply(400, name + " has the wrong token type; nothing saved.")
        if not ENV.exists():
            ENV.touch(mode=0o600)
        os.chmod(ENV, 0o600)
        for name, value in values.items():
            if value:
                set_key(str(ENV), name, value)
        os.chmod(ENV, 0o600)
        self.reply(
            200,
            "<!doctype html><title>Saved</title><h1>Configuration saved locally</h1><p>No credentials were displayed or sent to another service.</p>",
        )


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.expected_host = "127.0.0.1:" + str(server.server_port)
    print("http://" + server.expected_host + ROUTE, flush=True)
    server.serve_forever()
