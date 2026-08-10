#!/usr/bin/env python3
"""Local GUI server for editing collectors/youtube/tools/json/video_groups.json.

Usage:
    python scripts/youtube_grouping_editor/server.py [--port 8766] [--no-browser]

Zero extra dependencies on purpose (stdlib http.server) — this is a local,
single-user maintenance tool, not a deployed service. See
scripts/discography_editor/server.py for the sibling tool this mirrors.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

_lock = threading.Lock()
_catalog: catalog.Catalog | None = None


def _reload() -> catalog.Catalog:
    global _catalog
    _catalog = catalog.load_catalog()
    return _catalog


class Handler(BaseHTTPRequestHandler):
    server_version = "YoutubeGroupingEditor/1.0"

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("[ytgroup] " + (fmt % args) + "\n")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel_name: str) -> None:
        path = (STATIC_DIR / rel_name).resolve()
        if STATIC_DIR not in path.parents or not path.is_file():
            self.send_error(404)
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(path.suffix, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/" or self.path == "":
            self._send_static("index.html")
        elif self.path == "/app.js":
            self._send_static("app.js")
        elif self.path == "/style.css":
            self._send_static("style.css")
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif self.path == "/api/state":
            with _lock:
                assert _catalog is not None
                self._send_json(catalog.serialize_state(_catalog))
        else:
            self.send_error(404)

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_POST(self):  # noqa: N802
        if self.path == "/api/save":
            try:
                body = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json({"ok": False, "errors": ["JSON invalide envoyé par le client."]}, status=400)
                return

            groups = body.get("groups") or []
            with _lock:
                assert _catalog is not None
                ok, errors = catalog.save_groups(_catalog, groups)
                if ok:
                    _reload()
                    self._send_json({"ok": True, "errors": [], "state": catalog.serialize_state(_catalog)})
                else:
                    self._send_json({"ok": False, "errors": errors})
            return

        self.send_error(404)


def main() -> None:
    parser = argparse.ArgumentParser(description="Éditeur graphique local de video_groups.json (YouTube)")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    print("[ytgroup] chargement du catalogue de vidéos...")
    _reload()
    assert _catalog is not None
    print(f"[ytgroup] {len(_catalog.videos)} vidéos connues, {len(_catalog.groups)} groupe(s) existant(s).")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[ytgroup] serveur lancé sur {url}")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ytgroup] arrêt.")


if __name__ == "__main__":
    main()
