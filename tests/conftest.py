"""Shared fixtures, including a tiny in-process fake OpenBao server."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

TOKEN = "s.test-token"


class FakeBao:
    """Records requests and serves canned responses keyed by path."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, Any]] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.url = ""

    def kv2(self, mount: str, path: str, data: dict[str, Any]) -> None:
        self.routes[f"/v1/sys/internal/ui/mounts/{mount}"] = (
            200,
            {"data": {"options": {"version": "2"}}},
        )
        self.routes[f"/v1/{mount}/data/{path}"] = (200, {"data": {"data": data, "metadata": {}}})

    def kv1(self, mount: str, path: str, data: dict[str, Any]) -> None:
        self.routes[f"/v1/sys/internal/ui/mounts/{mount}"] = (
            200,
            {"data": {"options": None}},
        )
        self.routes[f"/v1/{mount}/{path}"] = (200, {"data": data})


@pytest.fixture
def fake_bao() -> Iterator[FakeBao]:
    state = FakeBao()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            headers = {k.lower(): v for k, v in self.headers.items()}
            state.requests.append((self.path, headers))
            if headers.get("x-vault-token") != TOKEN:
                self._send(403, {"errors": ["permission denied"]})
                return
            if self.path == "/v1/auth/token/lookup-self":
                self._send(200, {"data": {"ttl": 3600}})
                return
            if self.path == "/v1/redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/steal")
                self.end_headers()
                return
            status, body = state.routes.get(self.path, (404, {"errors": []}))
            self._send(status, body)

        def _send(self, status: int, body: Any) -> None:
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".envbao"
    d.mkdir(mode=0o700)
    d.chmod(0o700)
    return d


def write_config(directory: Path, text: str, mode: int = 0o600) -> Path:
    path = directory / "config.toml"
    path.write_text(text)
    path.chmod(mode)
    return path
