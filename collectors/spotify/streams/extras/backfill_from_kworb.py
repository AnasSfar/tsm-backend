#!/usr/bin/env python3
"""
Détecte les tracks Taylor Swift présents sur kworb mais absents de songs.json,
et les ajoute dans songs.json (section "kworb_extras").

Usage :
    python backfill_from_kworb.py
    python backfill_from_kworb.py --dry-run   # affiche sans écrire
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Dépendances manquantes : pip install requests beautifulsoup4")
    sys.exit(1)

# ── Paths ──────────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT  = _SCRIPT_DIR.parents[3]
DB_ROOT         = _REPO_ROOT / "db"
DISCOGRAPHY_DIR = DB_ROOT / "discography"
SONGS_JSON_PATH = DISCOGRAPHY_DIR / "songs.json"
ALBUMS_DIR_PATH = DISCOGRAPHY_DIR / "albums"

# ── Config ─────────────────────────────────────────────────────────────────────
ARTIST_ID       = "06HL4z0CvFAxyc27GXpf02"
KWORB_SONGS_URL = f"https://kworb.net/spotify/artist/{ARTIST_ID}_songs.html"
TRACK_ID_RE     = re.compile(r"spotify\.com/track/([A-Za-z0-9]+)")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s\-]+", "_", text.strip())
    return text


def clean_title(title: str) -> str:
    """Normalise les apostrophes/guillemets mal encodés depuis Kworb."""
    # Mojibake: octets UTF-8 de U+2019 (â\x80\x99) lus en Latin-1
    title = title.replace("\xe2\x80\x99", "'")   # ' right single quote
    title = title.replace("\xe2\x80\x98", "'")   # ' left single quote
    title = title.replace("\xe2\x80\x9c", '"')   # " left double quote
    title = title.replace("\xe2\x80\x9d", '"')   # " right double quote
    # Cas résiduel : \xe2 seul devant s/t/d (apostrophe coupée)
    title = re.sub(r"\xe2(?=[stdnlST])", "'", title)
    # Autres caractères de contrôle résiduels
    title = re.sub(r"[\x80-\x9f]", "", title)
    # Normalise les guillemets typographiques Unicode
    title = title.replace("\u2019", "'").replace("\u2018", "'")
    title = title.replace("\u201c", '"').replace("\u201d", '"')
    return title.strip()


_SYMBOL_RE = re.compile(r"[$@#%^*~`|]|[\u2000-\u303f]", re.UNICODE)


def has_bad_symbols(title: str) -> bool:
    """Retourne True si le titre contient des symboles/émojis non désirés."""
    return bool(_SYMBOL_RE.search(title))


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


# ── Parser ─────────────────────────────────────────────────────────────────────

def parse_songs_page(html: str) -> list[dict]:
    """
    Retourne une liste de { track_id, title, is_feature } depuis la page _songs.html.
    Les features ont un `*` devant le lien.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    for a in soup.find_all("a", href=TRACK_ID_RE):
        m = TRACK_ID_RE.search(a["href"])
        if not m:
            continue
        track_id = m.group(1)
        if track_id in seen:
            continue
        seen.add(track_id)

        title = clean_title(a.get_text(strip=True))
        if has_bad_symbols(title):
            continue
        # Détecte feature : le texte brut de la cellule parente commence par *
        parent_text = a.parent.get_text(strip=True) if a.parent else ""
        is_feature = parent_text.startswith("*")

        results.append({
            "track_id":   track_id,
            "title":      title,
            "is_feature": is_feature,
        })

    return results


# ── JSON helpers ───────────────────────────────────────────────────────────────

def existing_track_ids() -> set[str]:
    ids: set[str] = set()
    if ALBUMS_DIR_PATH.exists():
        for album_file in sorted(ALBUMS_DIR_PATH.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            for section in payload.get("sections", []) if isinstance(payload, dict) else []:
                for t in section.get("tracks", []):
                    url = (t.get("url") or t.get("spotify_url") or "").strip()
                    m = TRACK_ID_RE.search(url)
                    if m:
                        ids.add(m.group(1))

    if SONGS_JSON_PATH.exists():
        for section in json.loads(SONGS_JSON_PATH.read_text(encoding="utf-8")):
            for t in section.get("tracks", []):
                url = (t.get("url") or t.get("spotify_url") or "").strip()
                m = TRACK_ID_RE.search(url)
                if m:
                    ids.add(m.group(1))
    return ids


def existing_title_slugs() -> set[str]:
    slugs: set[str] = set()

    all_sections: list[dict] = []
    if ALBUMS_DIR_PATH.exists():
        for album_file in sorted(ALBUMS_DIR_PATH.glob("*.json"), key=lambda p: p.name.casefold()):
            try:
                payload = json.loads(album_file.read_text(encoding="utf-8-sig"))
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

    if SONGS_JSON_PATH.exists():
        all_sections.extend(json.loads(SONGS_JSON_PATH.read_text(encoding="utf-8")))

    for section in all_sections:
            for t in section.get("tracks", []):
                title = (t.get("title") or "").strip()
                if title:
                    slugs.add(slugify(title))
    return slugs


def add_to_songs_json(new_tracks: list[dict]) -> int:
    data: list[dict] = json.loads(SONGS_JSON_PATH.read_text(encoding="utf-8"))

    # Cherche ou crée la section kworb_extras
    group = next(
        (g for g in data
         if g.get("album") == "Standalone & Extras"
         and g.get("section") == "kworb_extras"),
        None,
    )
    if group is None:
        group = {"album": "Standalone & Extras", "section": "kworb_extras",
                 "track_count": 0, "tracks": []}
        data.append(group)

    existing_ids = {
        m.group(1)
        for t in group["tracks"]
        if (m := TRACK_ID_RE.search(t.get("url", "")))
    }

    added = 0
    for track in new_tracks:
        if track["track_id"] in existing_ids:
            continue

        group["tracks"].append({
            "title":           track["title"],
            "url":             f"https://open.spotify.com/intl-fr/track/{track['track_id']}",
            "type":            "standalone",
            "edition":         "extras",
            "display_section": "Kworb Extras",
            "display_order":   9999,
            "base_title":      track["title"],
            "album":           "Standalone & Extras",
            "primary_artist":  "Taylor Swift",
            "featured_artists": [],
            "artists":         ["Taylor Swift"],
            "title_clean":     track["title"],
            "song_family":     slugify(track["title"]),
            "version_tag":     None,
        })
        existing_ids.add(track["track_id"])
        added += 1

    group["track_count"] = len(group["tracks"])
    SONGS_JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return added


# ── Main ───────────────────────────────────────────────────────────────────────

def run(dry_run: bool) -> None:
    print(f"Récupération de {KWORB_SONGS_URL} …")
    html = fetch(KWORB_SONGS_URL)

    kworb_tracks = parse_songs_page(html)
    print(f"{len(kworb_tracks)} tracks trouvés sur kworb.\n")

    known_ids   = existing_track_ids()
    known_slugs = existing_title_slugs()

    new_tracks = [
        t for t in kworb_tracks
        if t["track_id"] not in known_ids
        and slugify(t["title"]) not in known_slugs
    ]

    if not new_tracks:
        print("Aucun track manquant. songs.json est à jour.")
        return

    print(f"{len(new_tracks)} tracks manquants :")
    for t in new_tracks:
        feat = " [feature]" if t["is_feature"] else ""
        print(f"  + {t['title']}{feat}  ({t['track_id']})")

    if dry_run:
        print("\n[DRY-RUN] Aucune écriture.")
        return

    added = add_to_songs_json(new_tracks)

    print(f"\n{added} tracks ajoutés à songs.json (section kworb_extras)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Détecte les tracks manquants depuis kworb")
    parser.add_argument("--dry-run", action="store_true", help="Simule sans écrire")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
