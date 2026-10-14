"""Build assets/timeline/: the Taylor Swift timeline (events JSON) + real photos for every event.

Input : taylor_swift_timeline_en_2006_2026.json (repo root, owner's list, 2026-10-13)
Output: assets/timeline/timeline.json  (events + era + images with sizes, kind, source, licence)
        assets/timeline/images/<image id>_{1600,800,400}.jpg  (gitignored, like every .jpg)

Image sources per event (SOURCES below, hand-mapped):
  spotify  -> album cover from db/discography/covers.json, full size (i.scdn.co ..82c1)
  track    -> single artwork (song title in db/discography songs) full size
  wiki     -> en.wikipedia page: main image (film poster, single/album cover, ceremony...)
              + the other photos shown on the page
  commons  -> Wikimedia Commons categories (concert / ceremony / wedding photos, free licences)
Each image is stored once (id = hash of its URL) in 3 sizes (long edge 1600 / 800 / 400, never
upscaled); credits + licence come from Commons metadata. Re-run = only missing files downloaded.

  python scripts/build_timeline_assets.py            # build / complete
  python scripts/build_timeline_assets.py --dry-run  # list what each event would get
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "taylor_swift_timeline_en_2006_2026.json"
OUT = REPO / "assets" / "timeline"
IMG_DIR = OUT / "images"
SIZES = (1600, 800, 400)
UA = {"User-Agent": "SwiftiesCharts-timeline/1.0 (anassfar.fr@gmail.com)"}
MAX_COMMONS = 5      # photos per Commons category list, per event
MAX_WIKI_EXTRA = 3   # extra photos from the Wikipedia page, besides its main image

sys.path.insert(0, str(REPO / "collectors" / "spotify" / "streams" / "tools" / "scripts"))
sys.path.insert(0, str(REPO / "collectors"))  # comp.*

# date|title-start -> sources. Keys are "YYYY-MM-DD" (+ "#n" when a date has several events).
S = dict  # shorthand
SOURCES: dict[str, dict] = {
    "2006-10-24": S(spotify=["taylor_swift"], wiki=["Taylor Swift (album)"], commons=["Taylor Swift in 2006"]),
    "2008-09-12": S(track=["Love Story"], wiki=["Love Story (Taylor Swift song)"], commons=["Taylor Swift in 2008"]),
    "2008-11-11": S(spotify=["fearless"], wiki=["Fearless (Taylor Swift album)"], commons=["Taylor Swift in 2008"]),
    "2009-04-23": S(wiki=["Fearless Tour"], commons=["Fearless Tour - North Charleston April 30th, 2009", "Fearless Tour - Jacksonville May 1st, 2009", "Fearless Tour - Los Angeles May 29th, 2009"]),
    "2009-09-13": S(wiki=["2009 MTV Video Music Awards"], commons=["Taylor Swift in 2009"]),
    "2010-01-31": S(wiki=["52nd Annual Grammy Awards"], spotify=["fearless"], commons=["Taylor Swift in 2010"]),
    "2010-07-10": S(wiki=["Fearless Tour"], commons=["Fearless Tour - Foxboro June 5th, 2010", "Fearless Tour - Los Angeles April 16th, 2010", "Fearless Tour - Austin, March 10th, 2010"]),
    "2010-10-25": S(spotify=["speak_now"], wiki=["Speak Now"], commons=["Taylor Swift in 2010"]),
    "2011-02-09": S(wiki=["Speak Now World Tour"], commons=["Speak Now World Tour - Paris March 17th, 2011", "Speak Now World Tour - Pittsburgh June 18th, 2011", "Speak Now World Tour - Newark July 20th, 2011"]),
    "2012-03-18": S(wiki=["Speak Now World Tour"], commons=["Speak Now World Tour - Sydney March 9th, 2012", "Speak Now World Tour - Vancouver September 11th, 2011", "Speak Now World Tour - Edmonton August 18th, 2011"]),
    "2012-10-22": S(spotify=["red"], wiki=["Red (Taylor Swift album)"], commons=["Taylor Swift in 2012"]),
    "2013-03-13": S(wiki=["The Red Tour"], commons=["Taylor Swift in 2013"]),
    "2014-06-12": S(wiki=["The Red Tour"], commons=["Taylor Swift in 2014"]),
    "2014-08-18": S(track=["Shake It Off"], wiki=["Shake It Off"], spotify=["1989"]),
    "2014-10-27": S(spotify=["1989"], wiki=["1989 (Taylor Swift album)"], commons=["Taylor Swift in 2014"]),
    "2015-05-05": S(wiki=["The 1989 World Tour"], commons=["The 1989 World Tour - Ford Field, Detroit (May 30, 2015)", "The 1989 World Tour - SSE Hydro, Glasgow (June 23, 2015)"]),
    "2015-12-12": S(wiki=["The 1989 World Tour"], commons=["The 1989 World Tour - Staples Center, Los Angeles (August 22, 2015)", "The 1989 World Tour - Minute Maid Park, Houston (September 9, 2015)"]),
    "2016-02-15": S(wiki=["58th Annual Grammy Awards"], spotify=["1989"], commons=["Taylor Swift in 2016"]),
    "2017-08-23": S(spotify=["reputation"], wiki=["Reputation (album)", "Look What You Made Me Do"], commons=["Taylor Swift in 2017"]),
    "2017-11-10": S(spotify=["reputation"], wiki=["Reputation (album)"], commons=["Taylor Swift in 2017"]),
    "2018-05-08": S(wiki=["Reputation Stadium Tour"], commons=["Reputation Stadium Tour - Levi's Stadium, Santa Clara (May 12, 2018)", "Reputation Stadium Tour - CenturyLink Field (Seattle) - 2018-05-22"]),
    "2018-11-19": S(wiki=["Taylor Swift masters dispute"], commons=["Reputation Stadium Tour - Sports Authority Field at Mile High, Denver (May 25, 2018)"]),
    "2018-11-21": S(wiki=["Reputation Stadium Tour"], commons=["Reputation Stadium Tour - MetLife Stadium, East Rutherford, NJ (July 22, 2018)", "Reputation Stadium Tour - U.S. Bank Stadium, Minneapolis (August 31, 2018)", "Reputation Stadium Tour - Raymond James Stadium, Tampa (August 14, 2018)"]),
    "2019-06-13": S(spotify=["lover"], wiki=["Lover (album)", "Me! (Taylor Swift song)"], commons=["Taylor Swift in 2019"]),
    "2019-06-30": S(wiki=["Taylor Swift masters dispute"], commons=["Taylor Swift in 2019"]),
    "2019-08-21": S(wiki=["Taylor Swift masters dispute", "Taylor's Version"], commons=["Taylor Swift in 2019"]),
    "2019-08-23": S(spotify=["lover"], wiki=["Lover (album)"], commons=["Taylor Swift in 2019"]),
    "2020-07-23": S(spotify=["folklore"], wiki=["Folklore (Taylor Swift album)"]),
    "2020-07-24": S(spotify=["folklore"], wiki=["Folklore (Taylor Swift album)", "Cardigan (song)"]),
    "2020-11-16": S(wiki=["Taylor Swift masters dispute"]),
    "2020-12-10": S(spotify=["evermore"], wiki=["Evermore (album)"]),
    "2020-12-11": S(spotify=["evermore"], wiki=["Evermore (album)", "Willow (Taylor Swift song)"]),
    "2021-02-11": S(spotify=["fearless_taylor_s_version"], wiki=["Fearless (Taylor's Version)"]),
    "2021-03-14": S(wiki=["63rd Annual Grammy Awards", "Folklore (Taylor Swift album)"], spotify=["folklore"]),
    "2021-04-09": S(spotify=["fearless_taylor_s_version"], wiki=["Fearless (Taylor's Version)"]),
    "2021-06-18": S(spotify=["red_taylor_s_version"], wiki=["Red (Taylor's Version)"]),
    "2021-11-12": S(spotify=["red_taylor_s_version"], wiki=["Red (Taylor's Version)", "All Too Well: The Short Film"]),
    "2022-08-28": S(wiki=["2022 MTV Video Music Awards", "Midnights"], spotify=["midnights"], commons=["Taylor Swift in 2022"]),
    "2022-10-21": S(spotify=["midnights"], wiki=["Midnights", "Anti-Hero (song)"]),
    "2022-11-01": S(wiki=["The Eras Tour"], commons=["The Eras Tour"]),
    "2023-03-17": S(wiki=["The Eras Tour"], commons=["The Eras Tour - Arlington, Texas - March 31, 2023", "The Eras Tour - Arlington, Texas - April 2, 2023", "The Eras Tour - Tampa, Florida - April 14, 2023"]),
    "2023-05-05": S(spotify=["speak_now_taylor_s_version"], wiki=["Speak Now (Taylor's Version)"], commons=["The Eras Tour - Philadelphia, Pennsylvania - May 12, 2023"]),
    "2023-07-07": S(spotify=["speak_now_taylor_s_version"], wiki=["Speak Now (Taylor's Version)"]),
    "2023-08-09": S(spotify=["1989_taylor_s_version"], wiki=["1989 (Taylor's Version)"], commons=["The Eras Tour - Inglewood, California - August 9, 2023"]),
    "2023-08-31": S(wiki=["Taylor Swift: The Eras Tour"], commons=["The Eras Tour - Inglewood, California - August 9, 2023 (2)"]),
    "2023-10-13": S(wiki=["Taylor Swift: The Eras Tour"], commons=["The Eras Tour - Inglewood, California - August 9, 2023", "The Eras Tour - Inglewood, California - August 9, 2023 (2)"]),
    "2023-10-27": S(spotify=["1989_taylor_s_version"], wiki=["1989 (Taylor's Version)"]),
    "2024-02-04#0": S(wiki=["66th Annual Grammy Awards"], spotify=["midnights"], commons=["Taylor Swift in 2024"]),
    "2024-02-04#1": S(spotify=["the_tortured_poets_department"], wiki=["The Tortured Poets Department"]),
    "2024-04-19": S(spotify=["the_tortured_poets_department"], wiki=["The Tortured Poets Department", "Fortnight (song)"]),
    "2024-12-08": S(wiki=["The Eras Tour"], commons=["The Eras Tour - New Orleans - October 26, 2024", "The Eras Tour - Gelsenkirchen - July 17, 2024", "The Eras Tour - Paris - May 11, 2024"]),
    "2025-05-30": S(wiki=["Taylor Swift masters dispute"]),
    "2025-08-12": S(spotify=["the_life_of_a_showgirl"], wiki=["The Life of a Showgirl"]),
    "2025-08-13": S(spotify=["the_life_of_a_showgirl"], wiki=["The Life of a Showgirl"]),
    "2025-08-26": S(wiki=["Taylor Swift and Travis Kelce", "Travis Kelce"]),
    "2025-09-19": S(wiki=["Taylor Swift: The Official Release Party of a Showgirl"]),
    "2025-10-03#0": S(spotify=["the_life_of_a_showgirl"], wiki=["The Life of a Showgirl", "The Fate of Ophelia"]),
    "2025-10-03#1": S(wiki=["Taylor Swift: The Official Release Party of a Showgirl"]),
    "2025-10-13#0": S(spotify=["the_life_of_a_showgirl"], wiki=["The Life of a Showgirl"]),
    "2025-10-13#1": S(wiki=["Taylor Swift: The End of an Era", "Taylor Swift: The Eras Tour"], commons=["The Eras Tour - New Orleans - October 26, 2024"]),
    "2025-12-12#0": S(wiki=["Taylor Swift: The Eras Tour"], commons=["The Eras Tour - New Orleans - October 26, 2024"]),
    "2025-12-12#1": S(wiki=["Taylor Swift: The End of an Era"], commons=["The Eras Tour - Gelsenkirchen - July 17, 2024"]),
    "2026-01-21": S(wiki=["Songwriters Hall of Fame"]),
    "2026-02-28": S(track=["Opalite"], wiki=["Opalite (song)"]),
    "2026-06-11": S(wiki=["Songwriters Hall of Fame"]),
    "2026-07-03": S(commons=["Wedding of Taylor Swift and Travis Kelce"], wiki=["Taylor Swift and Travis Kelce"]),
    "2026-09-23": S(spotify=["the_life_of_a_showgirl"], wiki=["The Life of a Showgirl"]),
    "2026-09-25": S(spotify=["the_life_of_a_showgirl"], wiki=["The Life of a Showgirl"]),
    "2026-09-27#0": S(track=["Patient Zero"], wiki=["Patient Zero (song)", "2026 MTV Video Music Awards"]),
    "2026-09-27#1": S(wiki=["2026 MTV Video Music Awards"], track=["Patient Zero"]),
    "2026-10-05": S(track=["Patient Zero"], wiki=["Patient Zero (song)"]),
}


# ── network ───────────────────────────────────────────────────────────────────
def get(url: str, *, binary: bool = False, tries: int = 3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                data = r.read()
            time.sleep(0.15)  # be polite with Wikimedia
            return data if binary else json.loads(data)
        except Exception as exc:
            if i == tries - 1:
                print(f"  [warn] {url[:110]} -> {exc}")
                return None
            time.sleep(2 * (i + 1))


def wapi(host: str, **params) -> dict:
    return get(f"https://{host}/w/api.php?" + urllib.parse.urlencode({**params, "format": "json"})) or {}


# ── sources ───────────────────────────────────────────────────────────────────
def _clean_meta(v: str) -> str:
    return re.sub(r"<[^>]+>", "", v or "").strip()


def file_infos(host: str, titles: list[str]) -> list[dict]:
    """imageinfo for File: titles (works for local + Commons files from en.wikipedia)."""
    out = []
    for i in range(0, len(titles), 40):
        d = wapi(host, action="query", titles="|".join(titles[i:i + 40]), prop="imageinfo",
                 iiprop="url|size|mime|extmetadata", iiurlwidth=1600,
                 iiextmetadatafilter="LicenseShortName|Artist|Credit|ImageDescription")
        for p in (d.get("query", {}).get("pages", {}) or {}).values():
            ii = (p.get("imageinfo") or [None])[0]
            if not ii or ii.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
                continue
            m = ii.get("extmetadata", {})
            out.append({"title": p["title"], "url": ii.get("thumburl") or ii["url"], "page": ii.get("descriptionurl"),
                        "width": ii["width"], "height": ii["height"],
                        "license": _clean_meta(m.get("LicenseShortName", {}).get("value")),
                        "credit": _clean_meta(m.get("Artist", {}).get("value"))[:160]})
    return out


def commons_category(cat: str, limit: int) -> list[dict]:
    d = wapi("commons.wikimedia.org", action="query", list="categorymembers", cmtitle="Category:" + cat,
             cmtype="file", cmlimit=200)
    titles = [m["title"] for m in d.get("query", {}).get("categorymembers", [])
              if re.search(r"\.(jpe?g|png)$", m["title"], re.I)]
    if not titles:
        return []
    infos = [f for f in file_infos("commons.wikimedia.org", titles) if min(f["width"], f["height"]) >= 600]
    # spread the picks through the category instead of the first N (same night, many near-duplicates)
    step = max(1, len(infos) // limit)
    return [dict(f, kind="photo") for f in infos[::step][:limit]]


def wiki_main_image(title: str) -> dict | None:
    """Page's lead image via the REST summary: it also returns NON-FREE files (film posters,
    single covers) that prop=pageimages leaves out. Those are small (~260 px wide, fair use)."""
    d = get("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_"), safe=""))
    img = (d or {}).get("originalimage")
    if not img:
        return None
    url = img["source"].split("?")[0]
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    kind = "poster" if re.search(r"poster|film|docuseries", name, re.I) else "page_main"
    return {"title": "File:" + name, "url": url, "width": img["width"], "height": img["height"], "kind": kind,
            "wiki": (d or {}).get("title"), "license": "fair use (Wikipedia)" if "/wikipedia/en/" in url else "",
            "low_res": max(img["width"], img["height"]) < 700}


def wiki_page(title: str, extra: int) -> list[dict]:
    d = wapi("en.wikipedia.org", action="query", titles=title, redirects=1, prop="images", imlimit=60)
    pages = list((d.get("query", {}).get("pages", {}) or {}).values())
    if not pages or "missing" in pages[0]:
        s = wapi("en.wikipedia.org", action="query", list="search", srsearch=title, srlimit=1)
        hits = s.get("query", {}).get("search", [])
        if not hits or hits[0]["title"] == title:
            print(f"  [warn] no Wikipedia page for {title!r}")
            return []
        return wiki_page(hits[0]["title"], extra)
    page = pages[0]
    out = []
    main = wiki_main_image(page["title"])
    if main:
        out.append(main)
    others = [im["title"] for im in page.get("images", [])
              if re.search(r"\.(jpe?g|png)$", im["title"], re.I)
              and not re.search(r"logo|icon|symbol|signature|flag|map|wordmark|award|star|question", im["title"], re.I)
              and (not main or im["title"].replace(" ", "_") != main["title"].replace(" ", "_"))]
    extras = 0
    for f in file_infos("en.wikipedia.org", others[:extra * 3]):
        if extras < extra and min(f["width"], f["height"]) >= 500:
            out.append(dict(f, kind="photo", wiki=page["title"]))
            extras += 1
    return out


def spotify_cover(key: str) -> list[dict]:
    from comp.discography import build_cover_map
    url = build_cover_map(REPO / "db" / "discography" / "covers.json").get(key)
    if not url:
        print(f"  [warn] no cover for {key!r}")
        return []
    big = re.sub(r"ab67616d0000b273", "ab67616d000082c1", url)  # full-size artwork
    return [{"title": key, "url": big, "fallback": url, "kind": "album_cover", "license": "© artwork", "credit": "Spotify"}]


_SONGS = None


def track_cover(title: str) -> list[dict]:
    global _SONGS
    if _SONGS is None:
        import generate_streams_image as gs
        _SONGS = gs.load_song_db()
    for v in _SONGS.values():
        if (v.get("title") or "").casefold() == title.casefold():
            url = v.get("single_image") or v.get("image_url")
            if url and url.startswith("http"):
                big = re.sub(r"ab67616d0000b273", "ab67616d000082c1", url)
                return [{"title": title, "url": big, "fallback": url, "kind": "single_cover", "license": "© artwork", "credit": "Spotify"}]
    print(f"  [warn] no track artwork for {title!r}")
    return []


def manual_images(key: str) -> list[dict]:
    """Owner-provided pictures (HD film posters...) in assets/timeline/manual/<date>[#n]/ ;
    file name prefix = kind (poster_, photo_, album_cover_, single_cover_), listed first."""
    folder = OUT / "manual" / key.replace("#", "_")
    out = []
    for f in sorted(folder.glob("*")) if folder.exists() else []:
        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            kind = next((k for k in ("poster", "album_cover", "single_cover", "photo") if f.stem.startswith(k)), "photo")
            out.append({"title": f.name, "url": f.resolve().as_uri(), "kind": kind, "manual": True, "credit": "manual"})
    return out


def about_taylor(img: dict) -> bool:
    """Photos / page images must name Taylor Swift in their file name: Wikipedia pages and year
    categories also hold other people (Hal David on the Songwriters Hall page, an opening act on
    a tour page...). Covers, posters and owner-provided files are always kept."""
    title = img.get("title") or ""
    if img.get("manual") or img.get("kind") in ("album_cover", "single_cover"):
        return True
    if img.get("kind") == "poster":  # her films only, not the ceremony's poster (2026 VMAs = Snoop Dogg)
        return bool(re.search(r"swift|showgirl|eras|end of an era", title, re.I))
    if re.search(r"guitar|museum|exhibit|songbook|trail|bracelet|costume|dress|wax|statue|mural|store|grove|outside|\bsign\b", title, re.I):
        return False  # objects / places / crowds, not her
    return bool(re.search(r"swift", title, re.I))


# ── storage ───────────────────────────────────────────────────────────────────
def store(img: dict, dry: bool) -> dict | None:
    iid = hashlib.sha1(img["url"].split("?")[0].encode()).hexdigest()[:12]
    files = {s: IMG_DIR / f"{iid}_{s}.jpg" for s in SIZES}
    meta = {k: img.get(k) for k in ("kind", "title", "license", "credit", "page", "wiki", "low_res", "manual") if img.get(k)}
    meta["id"], meta["source_url"] = iid, img["url"]
    if dry:
        return meta
    if not all(p.exists() for p in files.values()):
        data = (Path(urllib.request.url2pathname(img["url"][len("file:"):])).read_bytes() if img["url"].startswith("file:")
                else get(img["url"], binary=True)) or (get(img["fallback"], binary=True) if img.get("fallback") else None)
        if not data:
            return None
        try:
            im = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception as exc:
            print(f"  [warn] unreadable image {img['url'][:90]} ({exc})")
            return None
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        for s, path in files.items():
            c = im.copy()
            c.thumbnail((s, s), Image.LANCZOS)  # long edge <= s, never upscaled
            c.save(path, "JPEG", quality=90, optimize=True)
    w, h = Image.open(files[SIZES[0]]).size
    meta["orientation"] = "portrait" if h > w * 1.1 else "landscape" if w > h * 1.1 else "square"
    meta["sizes"] = {str(s): {"file": f"images/{p.name}", **dict(zip(("width", "height"), Image.open(p).size))}
                     for s, p in files.items()}
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    events = json.loads(SRC.read_text(encoding="utf-8-sig"))
    import day_theme
    seen: dict[str, int] = {}
    out = []
    for ev in events:
        n = seen.get(ev["date"], 0)
        seen[ev["date"]] = n + 1
        key = ev["date"] if f"{ev['date']}#0" not in SOURCES else f"{ev['date']}#{n}"
        src = SOURCES.get(key, {})
        print(f"{ev['date']} {ev['title']}")
        cands: list[dict] = manual_images(key)
        for k in src.get("spotify", []):
            cands += spotify_cover(k)
        for t in src.get("track", []):
            cands += track_cover(t)
        for i, t in enumerate(src.get("wiki", [])):
            cands += wiki_page(t, MAX_WIKI_EXTRA if i == 0 else 1)
        per_cat = max(2, MAX_COMMONS // max(1, len(src.get("commons", []))))
        for c in src.get("commons", []):
            cands += commons_category(c, per_cat)
        images, urls = [], set()
        for c in cands:
            if c["url"] in urls or not about_taylor(c):
                continue
            urls.add(c["url"])
            meta = store(c, args.dry_run)
            if meta:
                images.append(meta)
        print(f"  -> {len(images)} image(s): " + ", ".join(f"{m['kind']}" for m in images))
        slug = re.sub(r"[^a-z0-9]+", "-", ev["title"].lower()).strip("-")[:60]
        out.append({"id": f"{ev['date']}-{slug}", **ev, "era": day_theme.event_era(ev, events), "images": images})
    if not args.dry_run:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "timeline.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        keep = {sz["file"] for e in out for im in e["images"] for sz in im["sizes"].values()}
        for f in IMG_DIR.glob("*.jpg"):  # images no event uses any more (filter / source changes)
            if f"images/{f.name}" not in keep:
                f.unlink()
        print(f"\n{len(out)} events, {sum(len(e['images']) for e in out)} image refs, "
              f"{len(list(IMG_DIR.glob('*_1600.jpg')))} unique images -> {OUT}")


if __name__ == "__main__":
    main()
