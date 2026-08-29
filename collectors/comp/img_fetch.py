"""Shared, resilient image → base64 data-URI fetch for every PNG generator.

Why this module exists
----------------------
Every chart/card/table generator embeds cover art by downloading it at render
time and inlining it as a ``data:`` URI. Historically each generator had its own
copy of that logic with only an *in-process* cache: a single transient network
hiccup (WARP instability, Spotify throttling ``i.scdn.co``, an 8s timeout) made
the download raise, the ``except`` silently returned ``""`` and the template
rendered an empty placeholder — e.g. "The Fate of Ophelia" missing its cover on
the 2026-08-27 global chart image, Speak Now (TV) missing its cover on the
2026-08-26 album update image, while every other cover in the same run was fine.

This module fixes that for good:

* **Persistent on-disk cache** (``db/discography/.image_cache/``, one small file
  per URL). A cover fetched successfully once is never re-downloaded — so a
  later network blip can't blank a cover that already worked.
* **Retries** (``ATTEMPTS_PER_URL`` per candidate URL, not a single shot).
* **Spotify size-marker fallback**: if the exact CDN object fails, try the other
  standard sizes of the same image before giving up.

Only a genuine, total, first-time failure to fetch a never-before-seen image can
still yield an empty placeholder now.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen

_REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = _REPO_ROOT / "db" / "discography" / ".image_cache"

TIMEOUT_SECONDS = 8
ATTEMPTS_PER_URL = 3

_mem_cache: dict[str, str] = {}
_disk_lock = threading.Lock()


def re_sub_spotify_size(url: str, size_marker: str) -> str:
    return re.sub(r"ab67616d[0-9a-f]{8}", f"ab67616d{size_marker}", url, count=1)


def image_url_candidates(url: str) -> list[str]:
    """The URL itself, then the other standard Spotify CDN sizes of it."""
    candidates = [url]
    if "scdn.co/image/" in url or "spotifycdn.com/image/" in url:
        for size_marker in ("0000b273", "00001e02", "00004851", "00001e03"):
            alt = re_sub_spotify_size(url, size_marker)
            if alt not in candidates:
                candidates.append(alt)
    return candidates


def _cache_file(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.uri"


def _read_disk(url: str) -> str:
    try:
        text = _cache_file(url).read_text(encoding="utf-8")
    except Exception:
        return ""
    return text if text.startswith("data:") else ""


def _write_disk(url: str, data_uri: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _disk_lock:
            fd, tmp_name = tempfile.mkstemp(prefix=".img.", suffix=".tmp", dir=CACHE_DIR, text=True)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(data_uri)
            os.replace(tmp_name, _cache_file(url))
    except Exception:
        pass


def _download(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        mime = resp.headers.get_content_type() or "image/jpeg"
        data = base64.b64encode(resp.read()).decode()
    return f"data:{mime};base64,{data}"


def fetch_data_uri(url: str, *, persist: bool = True) -> str:
    """Return ``url`` as a base64 ``data:`` URI, or ``""`` if it truly can't be
    fetched. Non-http input is returned unchanged (already a path / data URI).

    Order: in-process cache → on-disk cache → download (retried, with Spotify
    size fallbacks). A successful fetch is written to the on-disk cache so it is
    never re-downloaded on a later run.
    """
    if not url or not isinstance(url, str):
        return ""
    if not url.startswith("http"):
        return url
    if url in _mem_cache:
        return _mem_cache[url]

    if persist:
        on_disk = _read_disk(url)
        if on_disk:
            _mem_cache[url] = on_disk
            return on_disk

    last_exc: Exception | None = None
    for candidate in image_url_candidates(url):
        for _ in range(ATTEMPTS_PER_URL):
            try:
                result = _download(candidate)
            except Exception as exc:  # noqa: BLE001 - any failure = try next
                last_exc = exc
                continue
            _mem_cache[url] = result
            _mem_cache[candidate] = result
            if persist:
                _write_disk(url, result)
            return result

    print(f"[warn] img_fetch: failed for {url} ({last_exc})")
    _mem_cache[url] = ""
    return ""
