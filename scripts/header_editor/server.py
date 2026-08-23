#!/usr/bin/env python3
"""Local GUI server for editing db/discography/headers images.

Usage:
    python scripts/header_editor/server.py [--port 8787] [--no-browser]

No extra dependencies. The browser crops/positions the selected image in a
canvas, then this server saves the final PNG into db/discography/headers.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import shutil
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

SCRIPT_DIR = Path(__file__).resolve().parent
STATIC_DIR = SCRIPT_DIR / "static"
REPO_ROOT = SCRIPT_DIR.parents[1]
ALBUMS_DIR = REPO_ROOT / "db" / "discography" / "albums"
HEADERS_DIR = REPO_ROOT / "db" / "discography" / "headers"
GLOBAL_HEADERS_DIR = REPO_ROOT / "collectors" / "spotify" / "streams" / "tools" / "headers"
COVERS_PATH = REPO_ROOT / "db" / "discography" / "covers.json"

OUTPUT_W = 2212
OUTPUT_H = 608
GLOBAL_TARGETS = [
    {"name": "Top Songs", "slug": "top_songs"},
    {"name": "Top Albums", "slug": "top_albums"},
    {"name": "Top Eras", "slug": "top_eras"},
]

_lock = threading.Lock()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _album_file_to_name(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict) and payload.get("album"):
            return str(payload["album"])
    except Exception:
        pass
    return path.stem.replace("_", " ").title()


def _album_dir_name(album_name: str) -> str:
    return (album_name or "").strip().casefold()


def _safe_file_stem(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9 _().'-]+", "_", (value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned or "header"


def _headers() -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not HEADERS_DIR.exists():
        return result
    for path in HEADERS_DIR.iterdir():
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            result[_norm(path.stem)] = path
    return result


def _known_album_names() -> set[str]:
    return {_album_file_to_name(path) for path in ALBUMS_DIR.glob("*.json")}


def _covers() -> dict[str, Any]:
    try:
        return json.loads(COVERS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _target_folder(target: dict[str, Any]) -> Path:
    if target["kind"] == "global":
        return GLOBAL_HEADERS_DIR / target["slug"]
    return HEADERS_DIR / _album_dir_name(target["name"])


def _target_by_name(name: str) -> dict[str, Any] | None:
    for target in GLOBAL_TARGETS:
        if target["name"] == name:
            return {"kind": "global", **target}
    for path in ALBUMS_DIR.glob("*.json"):
        album_name = _album_file_to_name(path)
        if album_name == name:
            return {"kind": "album", "name": album_name, "slug": _norm(album_name)}
    return None


def _target_by_slug(slug: str) -> dict[str, Any] | None:
    slug = _norm(slug)
    for target in GLOBAL_TARGETS:
        if target["slug"] == slug:
            return {"kind": "global", **target}
    for path in ALBUMS_DIR.glob("*.json"):
        album_name = _album_file_to_name(path)
        if _norm(album_name) == slug:
            return {"kind": "album", "name": album_name, "slug": slug}
    return None


def _target_variants(target: dict[str, Any]) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    folder = _target_folder(target)
    if folder.exists() and folder.is_dir():
        for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                variants.append({
                    "id": path.name,
                    "name": path.stem,
                    "url": f"/headers/{target['slug']}/{path.name}?v={int(path.stat().st_mtime)}",
                    "path": str(path),
                    "legacy": False,
                })
    if target["kind"] == "album":
        flat = _headers().get(target["slug"])
        if flat:
            variants.append({
                "id": "__flat__",
                "name": "Legacy flat header",
                "url": f"/headers/{target['slug']}/__flat__?v={int(flat.stat().st_mtime)}",
                "path": str(flat),
                "legacy": True,
            })
    return variants


def _albums_payload() -> dict[str, Any]:
    albums = []
    covers = _covers()
    targets = [{"kind": "global", **target} for target in GLOBAL_TARGETS]
    for path in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
        name = _album_file_to_name(path)
        targets.append({"kind": "album", "name": name, "slug": _norm(name)})

    for target in targets:
        variants = _target_variants(target)
        first = variants[0] if variants else None
        cover_url = ""
        if target["kind"] == "album":
            cover_url = str(covers.get(target["name"], {}).get("cover_url") or "")
        albums.append({
            "name": target["name"],
            "slug": target["slug"],
            "kind": target["kind"],
            "hasHeader": bool(variants),
            "headerUrl": first["url"] if first else "",
            "coverUrl": cover_url,
            "variants": variants,
            "output": {"width": OUTPUT_W, "height": OUTPUT_H},
        })
    return {"ok": True, "albums": albums, "output": {"width": OUTPUT_W, "height": OUTPUT_H}}


def _album_name_for_slug(slug: str) -> str | None:
    target = _target_by_slug(slug)
    return target["name"] if target else None


def _header_path_for_variant(slug: str, variant: str) -> Path | None:
    target = _target_by_slug(slug)
    if not target:
        return None
    if variant == "__flat__" and target["kind"] == "album":
        return _headers().get(target["slug"])
    candidate = (_target_folder(target) / variant).resolve()
    folder = _target_folder(target).resolve()
    if folder not in candidate.parents or not candidate.is_file():
        return None
    if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    return candidate


def _target_path_for_album(album_name: str, variant_name: str, selected_variant: str) -> Path:
    target = _target_by_name(album_name)
    if not target:
        raise ValueError("cible inconnue")
    folder = _target_folder(target)
    folder.mkdir(parents=True, exist_ok=True)

    selected_variant = selected_variant or ""
    if not variant_name.strip() and selected_variant and selected_variant != "__flat__":
        if Path(selected_variant).name != selected_variant:
            raise ValueError("nom de variante invalide")
        if Path(selected_variant).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("extension de variante invalide")
        return folder / selected_variant

    stem = _safe_file_stem(variant_name)
    if not variant_name.strip():
        base = target["slug"] or "header"
        existing = {
            p.stem.casefold()
            for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        }
        idx = 1
        while f"{base} {idx}".casefold() in existing:
            idx += 1
        stem = f"{base} {idx}"
    return folder / f"{stem}.png"


def _variant_payload_for_path(album_name: str, path: Path) -> dict[str, Any]:
    target = _target_by_name(album_name)
    slug = target["slug"] if target else _norm(album_name)
    return {
        "id": path.name,
        "name": path.stem,
        "url": f"/headers/{slug}/{path.name}?v={int(path.stat().st_mtime)}",
        "path": str(path),
        "legacy": False,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "HeaderEditor/1.0"

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("[headeredit] " + (fmt % args) + "\n")

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
        body = path.read_bytes()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(path.suffix, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"", "/"}:
            self._send_static("index.html")
            return
        if path == "/app.js":
            self._send_static("app.js")
            return
        if path == "/style.css":
            self._send_static("style.css")
            return
        if path == "/api/albums":
            self._send_json(_albums_payload())
            return
        if path.startswith("/headers/"):
            parts = path.removeprefix("/headers/").split("/", 1)
            slug = unquote(parts[0])
            variant = unquote(parts[1]) if len(parts) > 1 else "__flat__"
            header = _header_path_for_variant(slug, variant)
            if not header:
                self.send_error(404)
                return
            body = header.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(header.name)[0] or "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        if self.path == "/api/delete":
            self._handle_delete()
            return
        if self.path != "/api/save":
            self.send_error(404)
            return
        try:
            body = self._read_json_body()
            album = str(body.get("album") or "").strip()
            variant_name = str(body.get("variantName") or "").strip()
            selected_variant = str(body.get("selectedVariant") or "").strip()
            data_url = str(body.get("png") or "")
            if not album:
                raise ValueError("cible manquante")
            if not _target_by_name(album):
                raise ValueError("cible inconnue")
            m = re.fullmatch(r"data:image/png;base64,(.+)", data_url, flags=re.DOTALL)
            if not m:
                raise ValueError("PNG base64 manquant")
            data = base64.b64decode(m.group(1), validate=True)
            if len(data) < 100:
                raise ValueError("PNG trop petit ou invalide")
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        with _lock:
            target = _target_path_for_album(album, variant_name, selected_variant)
            backup = None
            if target.exists():
                backup = target.with_name(f"{target.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}")
                shutil.copy2(target, backup)
            target.write_bytes(data)
            variant = _variant_payload_for_path(album, target)
        self._send_json({
            "ok": True,
            "path": str(target),
            "backup": str(backup) if backup else "",
            "variant": variant,
            "albums": _albums_payload()["albums"],
        })

    def _handle_delete(self) -> None:
        try:
            body = self._read_json_body()
            album = str(body.get("album") or "").strip()
            variant_id = str(body.get("variantId") or "").strip()
            if not album:
                raise ValueError("cible manquante")
            target = _target_by_name(album)
            if not target:
                raise ValueError("cible inconnue")
            if not variant_id:
                raise ValueError("variante manquante")
            if variant_id == "__flat__":
                if target["kind"] != "album":
                    raise ValueError("variante invalide")
                path = _headers().get(target["slug"])
                if not path:
                    raise ValueError("fichier introuvable")
            else:
                folder = _target_folder(target).resolve()
                candidate = (folder / variant_id).resolve()
                if folder not in candidate.parents or not candidate.is_file():
                    raise ValueError("fichier introuvable")
                if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError("fichier invalide")
                path = candidate
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        with _lock:
            backup = path.with_name(f"{path.name}.deleted.{time.strftime('%Y%m%d-%H%M%S')}.bak")
            shutil.move(str(path), str(backup))
        self._send_json({
            "ok": True,
            "backup": str(backup),
            "albums": _albums_payload()["albums"],
        })


def main() -> None:
    parser = argparse.ArgumentParser(description="Header editor for album update images")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}/"
    print(f"[headeredit] output size: {OUTPUT_W}x{OUTPUT_H}")
    print(f"[headeredit] server running on {url}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[headeredit] stop.")


if __name__ == "__main__":
    main()
