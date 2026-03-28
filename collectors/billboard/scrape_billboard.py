"""Scrape Taylor Swift chart data from Billboard and append to billboard_history.csv."""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT   = _SCRIPT_DIR.parents[1]

BILLBOARD_CSV_PATH = _REPO_ROOT / "db" / "billboard_history.csv"
LIENS_PATH = _REPO_ROOT / "config" / "links" / "billboard.json"
liens = json.loads(LIENS_PATH.read_text(encoding="utf-8"))

URL_HOT_100    = liens.get("billboard hot 100", "")
URL_BB200      = liens.get("billboard 200", "")
URL_TS_HISTORY = liens.get("billboard taylor swift", "")
URL_GOAT       = liens.get("billboard greatest of all time artists", "")

HEADLESS = True
GOTO_TIMEOUT = 45_000


def _block_unneeded(route, request):
    if request.resource_type in ("image", "media", "font"):
        route.abort()
    else:
        route.continue_()


def _scrape_ranked_chart(page, url: str, filter_ts: bool) -> list[dict]:
    """Scrape a standard Billboard ranked chart (Hot 100 / Billboard 200)."""
    entries: list[dict] = []
    try:
        page.goto(url, timeout=GOTO_TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeoutError:
        print(f"  [timeout] {url}", flush=True)
        return entries

    try:
        page.wait_for_selector("li.o-chart-results-list__item", timeout=20_000)
    except PlaywrightTimeoutError:
        print(f"  [no results] {url}", flush=True)
        return entries

    items = page.query_selector_all("li.o-chart-results-list__item")
    for item in items:
        try:
            # Rank
            rank_el = item.query_selector("span.c-label.a-font-primary-bold-l")
            rank = int(rank_el.inner_text().strip()) if rank_el else None

            # Title
            title_el = item.query_selector("h3#title-of-a-story")
            title = title_el.inner_text().strip() if title_el else None

            # Artist
            artist_el = item.query_selector("span.c-label.a-no-truncate.a-bold")
            artist = artist_el.inner_text().strip() if artist_el else None

            # Weeks on chart (last detail cell)
            detail_cells = item.query_selector_all("li.o-chart-results-list__item")
            weeks_el = item.query_selector_all("span.chart-element__information__delta__text")
            weeks_on_chart = None
            if detail_cells:
                last_cell = detail_cells[-1]
                wk_text = last_cell.inner_text().strip()
                if wk_text.isdigit():
                    weeks_on_chart = int(wk_text)

            # Peak rank
            peak_rank = None
            peak_els = item.query_selector_all("span.c-label.a-font-primary-bold-l")
            if len(peak_els) >= 2:
                try:
                    peak_rank = int(peak_els[1].inner_text().strip())
                except ValueError:
                    pass

            if not title:
                continue

            if filter_ts and artist and "taylor swift" not in artist.lower():
                continue

            entries.append({
                "rank": rank,
                "title": title,
                "artist": artist,
                "weeks_on_chart": weeks_on_chart,
                "peak_rank": peak_rank,
            })
        except Exception as exc:
            print(f"  [row error] {exc}", flush=True)
            continue

    return entries


def _scrape_ts_chart_history(page, url: str) -> list[dict]:
    """Scrape Taylor Swift's chart history page (already filtered to TS)."""
    entries: list[dict] = []
    try:
        page.goto(url, timeout=GOTO_TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeoutError:
        print(f"  [timeout] {url}", flush=True)
        return entries

    # Chart history uses a table layout
    try:
        page.wait_for_selector("table tbody tr, .artist-chart-row", timeout=20_000)
    except PlaywrightTimeoutError:
        print(f"  [no results] {url}", flush=True)
        return entries

    rows = page.query_selector_all("table tbody tr")
    if not rows:
        rows = page.query_selector_all(".artist-chart-row")

    for row in rows:
        try:
            cells = row.query_selector_all("td")
            if len(cells) < 3:
                continue

            rank = None
            try:
                rank = int(cells[0].inner_text().strip())
            except (ValueError, IndexError):
                pass

            title = cells[1].inner_text().strip() if len(cells) > 1 else None
            chart = cells[2].inner_text().strip() if len(cells) > 2 else None

            weeks_on_chart = None
            peak_rank = None
            if len(cells) > 3:
                try:
                    weeks_on_chart = int(cells[3].inner_text().strip())
                except (ValueError, IndexError):
                    pass
            if len(cells) > 4:
                try:
                    peak_rank = int(cells[4].inner_text().strip())
                except (ValueError, IndexError):
                    pass

            if not title:
                continue

            entries.append({
                "rank": rank,
                "title": title,
                "chart": chart,
                "weeks_on_chart": weeks_on_chart,
                "peak_rank": peak_rank,
            })
        except Exception as exc:
            print(f"  [row error] {exc}", flush=True)
            continue

    return entries


def _scrape_greatest_artists(page, url: str) -> dict | None:
    """Find Taylor Swift's rank in the Greatest of All Time Artists chart."""
    try:
        page.goto(url, timeout=GOTO_TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeoutError:
        print(f"  [timeout] {url}", flush=True)
        return None

    try:
        page.wait_for_selector("li.o-chart-results-list__item", timeout=20_000)
    except PlaywrightTimeoutError:
        print(f"  [no results] {url}", flush=True)
        return None

    items = page.query_selector_all("li.o-chart-results-list__item")
    for item in items:
        try:
            name_el = item.query_selector("h3#title-of-a-story")
            if not name_el:
                continue
            name = name_el.inner_text().strip()
            if "taylor swift" not in name.lower():
                continue
            rank_el = item.query_selector("span.c-label.a-font-primary-bold-l")
            rank = int(rank_el.inner_text().strip()) if rank_el else None
            return {"rank": rank, "name": name}
        except Exception:
            continue
    return None


def _save_to_csv(result: dict) -> None:
    """Append today's scrape to billboard_history.csv, replacing any existing rows for today."""
    date = result["scraped_at"][:10]
    scraped_at = result["scraped_at"]

    fieldnames = ["date", "scraped_at", "chart_type", "rank", "title",
                  "artist", "weeks_on_chart", "peak_rank", "chart_label"]

    existing_rows: list[dict] = []
    if BILLBOARD_CSV_PATH.exists():
        with open(BILLBOARD_CSV_PATH, newline="", encoding="utf-8") as f:
            existing_rows = [r for r in csv.DictReader(f) if r.get("date") != date]

    new_rows: list[dict] = []
    for ct, entries in [("hot_100", result["hot_100"]), ("billboard_200", result["billboard_200"])]:
        for e in entries:
            new_rows.append({
                "date": date, "scraped_at": scraped_at, "chart_type": ct,
                "rank": e.get("rank", ""), "title": e.get("title", ""),
                "artist": e.get("artist", ""), "weeks_on_chart": e.get("weeks_on_chart", ""),
                "peak_rank": e.get("peak_rank", ""), "chart_label": "",
            })

    for e in result.get("ts_chart_history", []):
        new_rows.append({
            "date": date, "scraped_at": scraped_at, "chart_type": "ts_chart_history",
            "rank": e.get("rank", ""), "title": e.get("title", ""),
            "artist": "Taylor Swift", "weeks_on_chart": e.get("weeks_on_chart", ""),
            "peak_rank": e.get("peak_rank", ""), "chart_label": e.get("chart", ""),
        })

    ga = result.get("greatest_artists")
    if ga:
        new_rows.append({
            "date": date, "scraped_at": scraped_at, "chart_type": "greatest_artists",
            "rank": ga.get("rank", ""), "title": ga.get("name", "Taylor Swift"),
            "artist": "Taylor Swift", "weeks_on_chart": "", "peak_rank": "", "chart_label": "",
        })

    BILLBOARD_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BILLBOARD_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows + new_rows)

    print(f"\nSaved {len(new_rows)} rows to {BILLBOARD_CSV_PATH}", flush=True)


def main() -> None:
    result: dict = {
        "scraped_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        "hot_100": [],
        "billboard_200": [],
        "ts_chart_history": [],
        "greatest_artists": None,
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()
        page.route("**/*", _block_unneeded)

        # Hot 100 — filter to Taylor Swift only
        print("Scraping Hot 100...", flush=True)
        try:
            result["hot_100"] = _scrape_ranked_chart(page, URL_HOT_100, filter_ts=True)
            print(f"  {len(result['hot_100'])} TS entries found", flush=True)
        except Exception as exc:
            print(f"  [error] Hot 100: {exc}", flush=True)

        # Billboard 200 — filter to Taylor Swift only
        print("Scraping Billboard 200...", flush=True)
        try:
            result["billboard_200"] = _scrape_ranked_chart(page, URL_BB200, filter_ts=True)
            print(f"  {len(result['billboard_200'])} TS entries found", flush=True)
        except Exception as exc:
            print(f"  [error] Billboard 200: {exc}", flush=True)

        # TS Chart History — already filtered
        print("Scraping TS Chart History...", flush=True)
        try:
            result["ts_chart_history"] = _scrape_ts_chart_history(page, URL_TS_HISTORY)
            print(f"  {len(result['ts_chart_history'])} entries found", flush=True)
        except Exception as exc:
            print(f"  [error] TS Chart History: {exc}", flush=True)

        # Greatest of All Time Artists
        print("Scraping Greatest Artists...", flush=True)
        try:
            result["greatest_artists"] = _scrape_greatest_artists(page, URL_GOAT)
            if result["greatest_artists"]:
                print(f"  Taylor Swift: #{result['greatest_artists']['rank']}", flush=True)
            else:
                print("  Not found on page", flush=True)
        except Exception as exc:
            print(f"  [error] Greatest Artists: {exc}", flush=True)

        browser.close()

    _save_to_csv(result)


if __name__ == "__main__":
    main()
