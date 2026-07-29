#!/usr/bin/env python3
"""Local GUI server for editing db/discography/*.

Usage:
    python scripts/discography_editor/server.py [--port 8765] [--no-browser]

Zero extra dependencies on purpose (stdlib http.server) — this is a local,
single-user maintenance tool, not a deployed service.
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
    server_version = "DiscographyEditor/1.0"

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("[discoedit] " + (fmt % args) + "\n")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # /api/state must always reflect the current on-disk state — a cached
        # response here would show stale data after a save even though the
        # files themselves are correct (this bit us with app.js earlier).
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
        # This tool's static files change often during local iteration; never
        # let the browser silently serve a stale cached app.js after a restart.
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

            changes = body.get("changes") or {}
            with _lock:
                assert _catalog is not None
                ok, errors = catalog.save_changes(_catalog, changes)
                if ok:
                    _reload()
                    self._send_json({"ok": True, "errors": [], "state": catalog.serialize_state(_catalog)})
                else:
                    self._send_json({"ok": False, "errors": errors})
            return

        if self.path == "/api/mark-done":
            try:
                body = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json({"ok": False, "error": "JSON invalide envoyé par le client."}, status=400)
                return

            key = body.get("key")
            if not key:
                self._send_json({"ok": False, "error": "clé manquante"}, status=400)
                return
            with _lock:
                state = catalog.load_review_state()
                if body.get("done"):
                    state[str(key)] = True
                else:
                    state.pop(str(key), None)
                catalog.save_review_state(state)
            self._send_json({"ok": True})
            return

        self.send_error(404)


def main() -> None:
    parser = argparse.ArgumentParser(description="Éditeur graphique local de db/discography")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    print("[discoedit] chargement de la discographie...")
    _reload()
    assert _catalog is not None
    print(f"[discoedit] {len(_catalog.rows)} tracks chargés depuis {len(_catalog.order)} fichiers.")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[discoedit] serveur lancé sur {url}")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[discoedit] arrêt.")


if __name__ == "__main__":
    main()
