from __future__ import annotations

import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

PAGE_GOTO_TIMEOUT_MS = 20_000
DEBUG_PAGE_PREVIEW = False
LOG_MODE = "normal"


def parse_int_from_text(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def is_duration_line(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", text.strip()))


def is_large_number_line(text: str) -> bool:
    cleaned = text.strip().replace("\u202f", " ").replace("\xa0", " ")
    if not re.fullmatch(r"[\d\s,.\']+", cleaned):
        return False
    value = parse_int_from_text(cleaned)
    return value is not None and value >= 1000


def normalize_title(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = value.replace("â€™", "'").replace("â€˜", "'").replace("â€œ", '"').replace("â€", '"')
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_spotify_track_url(url: str) -> str:
    match = re.search(r"track/([A-Za-z0-9]+)", url or "")
    if match:
        return f"https://open.spotify.com/track/{match.group(1)}"
    return (url or "").strip()


def maybe_accept_cookies(page) -> None:
    for pattern in [r"Accept", r"Accept all", r"Accepter", r"Autoriser"]:
        try:
            page.get_by_role("button", name=re.compile(pattern, re.I)).click(timeout=1500)
            page.wait_for_timeout(1000)
            return
        except Exception:
            pass

def extract_main_track_playcount_from_lines(lines: list[str]) -> tuple[int | None, str | None]:
    if not lines:
        return None, None

    start_idx = None
    for i, line in enumerate(lines):
        if line.strip().lower() in ("titre", "title"):
            start_idx = i
            break

    if start_idx is None:
        return None, None

    end_markers = {
        "connectez-vous",
        "se connecter",
        "artiste",
        "recommandés",
        "basees sur ce titre",
        "basées sur ce titre",
        "titres populaires par",
        "sorties populaires par taylor swift",
    }

    block: list[str] = []
    for line in lines[start_idx + 1:]:
        normalized = normalize_title(line.strip())
        if normalized in end_markers:
            break
        block.append(line.strip())

    if not block:
        return None, None

    for i, line in enumerate(block):
        if is_duration_line(line):
            for j in range(i + 1, min(i + 6, len(block))):
                candidate = block[j].strip()
                if candidate in {"•", "-", ""}:
                    continue
                if is_large_number_line(candidate):
                    value = parse_int_from_text(candidate)
                    if value is not None:
                        return value, candidate

    numeric_candidates = []
    for line in block:
        cleaned = line.strip()
        if is_large_number_line(cleaned):
            value = parse_int_from_text(cleaned)
            if value is not None:
                numeric_candidates.append((value, cleaned))

    if len(numeric_candidates) == 1:
        return numeric_candidates[0]

    return None, None

def extract_recommended_tracks_from_lines(lines: list[str]) -> list[dict]:
    if not lines:
        return []

    start_idx = None
    for i, line in enumerate(lines):
        normalized = normalize_title(line)
        if normalized == "recommandes":
            start_idx = i
            break

    if start_idx is None:
        return []

    block: list[str] = []
    end_markers = {
        "titres populaires par",
        "sorties populaires par taylor swift",
        "sorties populaires par",
        "afficher plus",
    }

    for line in lines[start_idx + 1:]:
        normalized = normalize_title(line)
        if normalized in end_markers:
            break
        block.append(line.strip())

    if not block:
        return []

    results: list[dict] = []
    i = 0
    while i < len(block):
        title = block[i].strip()
        norm_title = normalize_title(title)

        if (
            not title
            or norm_title in {"basees sur ce titre", "basées sur ce titre", "e", "taylor swift"}
            or title in {"•", "-", "..."}
            or is_large_number_line(title)
            or is_duration_line(title)
            or title.isdigit()
        ):
            i += 1
            continue

        found_streams = None
        found_duration = None

        for j in range(i + 1, min(i + 8, len(block))):
            candidate = block[j].strip()

            if is_large_number_line(candidate) and found_streams is None:
                found_streams = parse_int_from_text(candidate)
                continue

            if is_duration_line(candidate) and found_duration is None:
                found_duration = candidate

            if found_streams is not None and found_duration is not None:
                break

        if found_streams is not None:
            results.append(
                {
                    "title": title,
                    "streams": found_streams,
                    "duration": found_duration,
                }
            )

        i += 1

    deduped = []
    seen = set()
    for row in results:
        key = normalize_title(row["title"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    return deduped

def extract_playcount_via_js(page) -> int | None:
    """
    Fallback: scan all [data-testid] elements for a single large numeric value
    that looks like a play count. Returns None if ambiguous or not found.
    """
    try:
        result = page.evaluate("""() => {
            const candidates = [];
            document.querySelectorAll('[data-testid]').forEach(el => {
                const txt = (el.innerText || '').trim();
                if (txt && /^[\\d\u202f\u00a0\\s,.']+$/.test(txt)) {
                    const n = parseInt(txt.replace(/[^\\d]/g, ''));
                    if (!isNaN(n) && n >= 10000) candidates.push(n);
                }
            });
            // Return only if exactly one candidate (unambiguous)
            return candidates.length === 1 ? candidates[0] : null;
        }""")
        if result is not None:
            return int(result)
    except Exception:
        pass
    return None

def extract_page_data(page) -> tuple[int | None, str | None, list[dict]]:
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None, None, []

    if not body_text:
        return None, None, []

    lines = [
        line.replace("\u202f", " ").replace("\xa0", " ").strip()
        for line in body_text.splitlines()
    ]
    lines = [line for line in lines if line]

    total, raw = extract_main_track_playcount_from_lines(lines)
    recs = extract_recommended_tracks_from_lines(lines)

    if total is None:
        # JS fallback: try finding the play count via data-testid attributes
        js_total = extract_playcount_via_js(page)
        if js_total is not None:
            total = js_total
            raw = str(js_total)

    return total, raw, recs

def debug_page_preview(page, title: str, url: str) -> None:
    if not DEBUG_PAGE_PREVIEW:
        return

    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        body_text = ""

    try:
        page_title = page.title()
    except Exception:
        page_title = ""

    print()
    print("=" * 80)
    print(f"TRACK: {title}")
    print(f"URL asked: {url}")
    print(f"URL final: {page.url}")
    print(f"PAGE TITLE: {page_title}")
    print("BODY PREVIEW:")
    print(body_text[:2500])
    print("=" * 80)
    print()

def scrape_track_total(page, title: str, url: str) -> tuple[int | None, str | None, str, list[dict]]:
    clean_url = normalize_spotify_track_url(url)

    for attempt in range(3):
        try:
            page.goto(clean_url, wait_until="commit", timeout=PAGE_GOTO_TIMEOUT_MS)
            page.wait_for_timeout(1000)
            maybe_accept_cookies(page)

            if DEBUG_PAGE_PREVIEW:
                debug_page_preview(page, title, clean_url)

            # Attendre activement l'apparition d'un grand nombre dans le DOM (max 8s)
            # Évite les sleeps fixes (500+1500+3000+5000ms) pour les pages rapides
            try:
                page.wait_for_function(
                    "() => { for (const el of document.querySelectorAll('[data-testid], span, div')) {"
                    "  const n = parseInt((el.innerText || '').replace(/[^\\d]/g, ''));"
                    "  if (!isNaN(n) && n >= 100000) return true; } return false; }",
                    timeout=8000,
                )
            except Exception:
                pass

            total, raw, recs = extract_page_data(page)
            if total is not None:
                return total, raw, "ok", recs

            # Fallback : 2 attentes courtes si le DOM n'était pas encore prêt
            for wait_ms in (1000, 2500):
                page.wait_for_timeout(wait_ms)
                total, raw, recs = extract_page_data(page)
                if total is not None:
                    return total, raw, "ok", recs

            return None, None, "not_found", []

        except PlaywrightTimeoutError:
            if LOG_MODE != "quiet":
                print(f"SCRAPE TIMEOUT on {title}: {clean_url} (attempt {attempt + 1}/3)")
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass

        except Exception as e:
            if LOG_MODE != "quiet":
                print(f"SCRAPE ERROR on {title}: {e} (attempt {attempt + 1}/3)")
            try:
                page.wait_for_timeout(1000)
            except Exception:
                pass

    return None, None, "timeout", []
