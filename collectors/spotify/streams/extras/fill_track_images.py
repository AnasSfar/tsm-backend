from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR   = Path(__file__).resolve().parent
_REPO_ROOT    = _SCRIPT_DIR.parents[2]
ROOT          = _REPO_ROOT / "website"
DISCO_DIR     = _REPO_ROOT / "db" / "discography"
ALBUMS_DIR    = DISCO_DIR / "albums"
SITE_SONGS    = ROOT / "site" / "data" / "songs.json"
_DISCO_FILES  = ["songs.json"]

HEADLESS = True
MAX_PARALLEL_PAGES = 4
START_TIME = None


def block_unneeded(route):
    request = route.request
    url = request.url.lower()
    resource_type = request.resource_type

    blocked_resource_types = {
        "image",
        "media",
        "font",
    }

    blocked_keywords = (
        "doubleclick",
        "googletagmanager",
        "google-analytics",
        "analytics",
        "facebook",
        "pixel",
        "ads",
        ".mp4",
        ".webm",
        ".mp3",
        ".wav",
        ".ogg",
        ".woff",
        ".woff2",
        ".ttf",
    )

    if resource_type in blocked_resource_types or any(x in url for x in blocked_keywords):
        route.abort()
    else:
        route.continue_()


def _track_id_from_url(url: str) -> str | None:
    m = re.search(r"track/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def load_tracks_missing_images() -> list[dict]:
    seen: dict[str, dict] = {}

    all_sections = []
    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            album_name = payload.get("album", "") if isinstance(payload, dict) else ""
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                if not isinstance(section, dict):
                    continue
                item = dict(section)
                if not item.get("album"):
                    item["album"] = album_name
                all_sections.append(item)

    for fname in _DISCO_FILES:
        path = DISCO_DIR / fname
        if not path.exists():
            continue
        try:
            all_sections.extend(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue

    for section in all_sections:
            for t in section.get("tracks", []):
                url      = (t.get("url") or t.get("spotify_url") or "").strip()
                track_id = _track_id_from_url(url)
                title    = (t.get("title") or "").strip()
                if not track_id or not title or track_id in seen:
                    continue
                image_url = (t.get("image_url") or "").strip()
                if image_url:
                    seen[track_id] = None  # already has image, skip
                    continue
                seen[track_id] = {
                    "track_id":    track_id,
                    "title":       title,
                    "spotify_url": f"https://open.spotify.com/track/{track_id}",
                    "image_url":   None,
                }
    return sorted(
        [v for v in seen.values() if v is not None],
        key=lambda x: x["title"].casefold(),
    )


def scrape_track_image(page, url: str) -> str | None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1000)

        og = page.locator('meta[property="og:image"]')
        if og.count() > 0:
            content = og.first.get_attribute("content")
            if content and content.strip():
                return content.strip()

        twitter = page.locator('meta[name="twitter:image"]')
        if twitter.count() > 0:
            content = twitter.first.get_attribute("content")
            if content and content.strip():
                return content.strip()

        return None

    except PlaywrightTimeoutError:
        return None
    except Exception:
        return None


def live_progress(i, total, title, result):
    elapsed = time.perf_counter() - START_TIME if START_TIME else 0

    if result is None:
        done = max(i - 1, 0)
    else:
        done = i

    if done > 0:
        avg = elapsed / done
        remaining = max(total - done, 0) * avg
    else:
        remaining = 0

    eta = f"{int(remaining // 60)}m {int(remaining % 60)}s"
    prefix = f"[{i}/{total}] {title}"

    if result is None:
        print(f"{prefix} ... fetching image | ETA {eta}")
        return

    status = result.get("status", "unknown")

    if status == "updated":
        print(f"{prefix} OK | image saved | ETA {eta}")
    elif status == "not_found":
        print(f"{prefix} NOT FOUND | ETA {eta}")
    else:
        print(f"{prefix} {status.upper()} | ETA {eta}")


def _worker(queue, results, lock, on_progress, total_tracks):
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=HEADLESS)
    context = browser.new_context(locale="fr-FR")
    page = context.new_page()
    page.route("**/*", block_unneeded)

    try:
        while True:
            try:
                i, track = queue.get_nowait()
            except Empty:
                break

            track_id = track["track_id"]
            title = track["title"]
            url = track["spotify_url"]

            if on_progress:
                on_progress(i, total_tracks, title, None)

            image_url = scrape_track_image(page, url)

            if image_url:
                result = {
                    "track_id": track_id,
                    "title": title,
                    "status": "updated",
                    "image_url": image_url,
                }
            else:
                result = {
                    "track_id": track_id,
                    "title": title,
                    "status": "not_found",
                }

            with lock:
                results[i - 1] = result

            if on_progress:
                on_progress(i, total_tracks, title, result)

            queue.task_done()

    finally:
        browser.close()
        p.stop()


def run_fill_images(on_progress=None):
    tracks = load_tracks_missing_images()
    total_tracks = len(tracks)
    results = [None] * total_tracks

    if total_tracks == 0:
        return []

    queue = Queue()
    for i, track in enumerate(tracks, 1):
        queue.put((i, track))

    lock = threading.Lock()
    worker_count = min(MAX_PARALLEL_PAGES, total_tracks)

    workers = [
        threading.Thread(
            target=_worker,
            args=(queue, results, lock, on_progress, total_tracks),
            daemon=True,
        )
        for _ in range(worker_count)
    ]

    for w in workers:
        w.start()

    for w in workers:
        w.join()

    return [r for r in results if r is not None]


def propagate_to_jsons(found: dict[str, str]) -> None:
    """Propagate track_id → image_url to discography JSONs and website songs.json."""
    if not found:
        return

    # db/discography/songs.json (list-of-sections format)
    for fname in _DISCO_FILES:
        path = DISCO_DIR / fname
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        sections = data if isinstance(data, list) else [data]
        for section in sections:
            for track in section.get("tracks", []):
                url = track.get("url", "")
                m = __import__("re").search(r"/track/([A-Za-z0-9]+)", url)
                if m and m.group(1) in found:
                    track["image_url"] = found[m.group(1)]
                    changed = True
        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Updated {path.name} ({len(found)} track(s))")

    # db/discography/albums/*.json (dict with sections)
    if ALBUMS_DIR.exists():
        for album_file in sorted(ALBUMS_DIR.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            changed = False
            for section in payload.get("sections", []):
                for track in section.get("tracks", []):
                    url = track.get("url") or track.get("spotify_url") or ""
                    m = re.search(r"/track/([A-Za-z0-9]+)", url)
                    if m and m.group(1) in found:
                        track["image_url"] = found[m.group(1)]
                        changed = True
            if changed:
                album_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"Updated {album_file.name} ({len(found)} track(s))")

    # website/site/data/songs.json (dict with "songs" list)
    if SITE_SONGS.exists():
        data = json.loads(SITE_SONGS.read_text(encoding="utf-8"))
        changed = False
        for song in data.get("songs", []):
            tid = song.get("track_id")
            if tid and tid in found:
                song["image_url"] = found[tid]
                changed = True
        if changed:
            SITE_SONGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Updated {SITE_SONGS.name}")


def main():
    global START_TIME
    START_TIME = time.perf_counter()

    results = run_fill_images(on_progress=live_progress)

    elapsed = time.perf_counter() - START_TIME
    updated = sum(1 for r in results if r["status"] == "updated")
    not_found = sum(1 for r in results if r["status"] == "not_found")

    # Propagate found images to all JSONs
    found = {r["track_id"]: r["image_url"] for r in results if r["status"] == "updated"}
    propagate_to_jsons(found)

    print()
    print(f"Finished in {int(elapsed // 60)}m {int(elapsed % 60)}s")
    print(f"Images updated: {updated}")
    print(f"Images not found: {not_found}")


if __name__ == "__main__":
    main()