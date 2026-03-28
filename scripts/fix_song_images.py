#!/usr/bin/env python3
import json
import math
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


SONGS_JSON = Path(__file__).parent.parent / "db" / "discography" / "songs.json"
HEADLESS = True
TIMEOUT_MS = 30000
SAVE_EVERY = 20
MAX_WORKERS = 6


lock = threading.Lock()


def load_songs(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("songs.json doit contenir une liste")
    return data


def save_songs(path: Path, songs: list[dict]) -> None:
    path.write_text(
        json.dumps(songs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_good_url(url: str) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def should_update(song: dict) -> bool:
    url = (song.get("url") or "").strip()
    img = (song.get("image_url") or "").strip()
    return is_good_url(url) and (not is_good_url(img))


def split_chunks(items: list, n: int) -> list[list]:
    if n <= 1:
        return [items]
    size = math.ceil(len(items) / n)
    return [items[i:i + size] for i in range(0, len(items), size)]


def extract_cover(page, url: str) -> str:
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    page.wait_for_timeout(1200)

    selectors = [
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
    ]

    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.count() > 0:
                content = loc.get_attribute("content")
                if content and content.startswith("http"):
                    return content.strip()
        except Exception:
            pass

    return ""


def worker(worker_id: int, chunk: list[tuple[int, dict]]) -> list[tuple[int, str, str]]:
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        for idx, song in chunk:
            title = song.get("title", "(sans titre)")
            url = (song.get("url") or "").strip()

            if not is_good_url(url):
                print(f"[W{worker_id}] SKIP  {idx} | {title} | url absente/invalide")
                continue

            try:
                image_url = extract_cover(page, url)
                if is_good_url(image_url):
                    print(f"[W{worker_id}] OK    {idx} | {title}")
                    results.append((idx, image_url, "ok"))
                else:
                    print(f"[W{worker_id}] MISS  {idx} | {title}")
                    results.append((idx, "", "miss"))

            except PlaywrightTimeoutError:
                print(f"[W{worker_id}] TIMEOUT {idx} | {title}")
                results.append((idx, "", "timeout"))
            except Exception as e:
                print(f"[W{worker_id}] ERROR {idx} | {title} | {e}")
                results.append((idx, "", "error"))

        context.close()
        browser.close()

    return results


def main():
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SONGS_JSON
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_WORKERS

    songs = load_songs(json_path)

    targets = [
        (i, song)
        for i, song in enumerate(songs)
        if should_update(song)
    ]

    if not targets:
        print("Aucune chanson à traiter")
        return

    workers = max(1, min(workers, len(targets)))
    chunks = split_chunks(targets, workers)

    print(f"Chansons à traiter : {len(targets)}")
    print(f"Workers            : {workers}")

    updated = 0
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(worker, wid + 1, chunk)
            for wid, chunk in enumerate(chunks)
        ]

        for future in as_completed(futures):
            results = future.result()

            with lock:
                for idx, image_url, status in results:
                    done += 1
                    if status == "ok" and is_good_url(image_url):
                        songs[idx]["image_url"] = image_url
                        updated += 1

                    if done % SAVE_EVERY == 0:
                        save_songs(json_path, songs)
                        print(f"[SAVE] progression: {done}/{len(targets)}")

    save_songs(json_path, songs)

    print()
    print("Terminé")
    print(f"Traitées : {len(targets)}")
    print(f"Maj      : {updated}")


if __name__ == "__main__":
    main()