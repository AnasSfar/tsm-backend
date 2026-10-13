"""Annotated thread cards (Top Collections, All Artists, YouTube): a real
phone-width capture of the live page, with numbered callouts + arrows
explaining the controls. Positions come from the live DOM (bounding boxes),
so arrows land on the real buttons. Portrait 1080x1350, rendered x2, plus a
310px phone copy.

    python previews_and_sims/data-tour-launch-post/make_feature_cards.py
"""
import base64
import json
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from make_post import BACKEND, ERA_COLORS, FONTS, data_uri, file_uri

HERE = Path(__file__).resolve().parent
OUT = HERE / "cards"
SITE = "https://thetsmuseum.app"
VW, VH = 390, 844          # captured phone viewport (CSS px)
SCALE = 1.15               # phone size on the card
PHONE_X, PHONE_Y = 315, 262

# era portrait index on the home strip (album order), accent for the card.
CARDS = [
    {
        "slug": "top_collections", "era": 8, "accent": "#A0704A",
        "kicker": "New in Spotify Streams", "title": "Top Collections",
        "sub": "Taylor's songs grouped and ranked by streams",
        "route": "/spotifystreams/top-collections",
        "callouts": [
            ("text=Top Collections >> nth=0", "A new tab in Spotify Streams"),
            (".toolbar-search", "Search any collection"),
            (".collections-kind-control", "Setlists, track numbers, songwriting, versions…"),
            (".songs-list > * >> nth=0", "Daily streams, change & total of each collection"),
        ],
    },
    {
        "slug": "all_artists", "era": 4, "accent": "#3E8FBF",
        "kicker": "New in Spotify Charts", "title": "All Artists",
        "sub": "Every song on the Spotify Daily Top 200, not just Taylor's",
        "route": "/spotifycharts/charts/all-artists",
        "click": "role=button[name='Everyone']",
        "callouts": [
            ("role=button[name='All Artists']", "The new All Artists view"),
            ("role=button[name='Everyone']", "Everyone, or Taylor only"),
            ("text=Other artist >> nth=0", "Follow any other artist"),
            ("role=button[name='Female'] >> nth=0 >> xpath=..", "Women / men, solo / groups"),
            ("text=Global >> nth=0", "Global or any country"),
            ("input[placeholder*='Search song']", "Search a song, artist or album"),
        ],
    },
    {
        "slug": "youtube", "era": 3, "accent": "#B3282D",
        "kicker": "New on YouTube Charts", "title": "Songs view",
        "sub": "Every song's clips and official audios, counted together",
        "route": "/youtube",
        "callouts": [
            ("role=button[name='Daily views']", "Daily or total views"),
            ("text=Showing >> xpath=..", "Songs, or only clips, audios, extras"),
            ("text=Oct 11, 2026 >> nth=0", "Go back to any day"),
            ("input[placeholder*='Search videos']", "Search any song or video"),
            (".songs-list > * >> nth=0", "All the uploads of a song in one line"),
        ],
    },
    {
        "slug": "album_rankings", "accent": "#C8577F",
        "kicker": "New in Players", "title": "Album Rankings",
        "sub": "Rank every song of every album, era by era",
        # shot + boxes come from play_ranking.py (full game played with writes blocked)
        "prebuilt": True,
        "callouts": [],
    },
]


def capture(card, page):
    page.goto(SITE + "/", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(900)
    page.locator(".erashome-strip-photo").nth(card["era"]).tap()
    page.wait_for_timeout(1100)
    page.goto(SITE + card["route"], wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2500)
    if card.get("click"):
        loc = page.locator(card["click"])
        if loc.count():
            loc.first.click()
            page.wait_for_timeout(1500)
    if card.get("scroll"):
        page.mouse.wheel(0, card["scroll"])
        page.wait_for_timeout(1000)
    shot = OUT / f"{card['slug']}_shot.png"
    page.screenshot(path=str(shot))
    boxes = []
    for sel, label in card["callouts"]:
        if not label:
            continue
        loc = page.locator(sel)
        box = loc.first.bounding_box() if loc.count() else None
        if not box or box["y"] > VH - 20 or box["y"] + box["height"] < 0:
            print("  skip (not visible):", card["slug"], sel)
            continue
        boxes.append({"label": label, **box})
    return shot, boxes


def layout(boxes):
    """Number in reading order (top -> bottom), alternate sides, then spread
    labels vertically on each side so they never overlap."""
    boxes.sort(key=lambda b: b["y"])
    count = {"left": 0, "right": 0}
    for b in boxes:
        cx = b["x"] + b["width"] / 2
        if cx < VW * 0.42:
            b["side"] = "left"          # element on the left: label on the left
        elif cx > VW * 0.58:
            b["side"] = "right"
        else:                           # full-width / centred: balance the sides
            b["side"] = "left" if count["left"] <= count["right"] else "right"
        count[b["side"]] += 1
    for side in ("left", "right"):
        prev = PHONE_Y - 10
        for b in [b for b in boxes if b["side"] == side]:
            ty = PHONE_Y + (b["y"] + min(b["height"] / 2, 34)) * SCALE
            b["ly"] = max(ty - 40, prev + 14)
            prev = b["ly"] + 120
    return boxes


def build_html(card, shot, boxes):
    shot_uri = data_uri(Image.open(shot).convert("RGB"))
    logo_uri = data_uri(Image.open(BACKEND / "db" / "logo.png"), "PNG")
    band = "".join(f'<i style="background:{c}"></i>' for c in ERA_COLORS)
    labels, arrows = [], []
    for i, b in enumerate(boxes, 1):
        left = b["side"] == "left"
        lx = 26 if left else PHONE_X + VW * SCALE + 22
        # arrow: from label edge to the target's near edge (mid height)
        ax = lx + 266 if left else lx - 6
        ay = b["ly"] + 26
        # dot just outside the element's edge (never on its text)
        tx = PHONE_X + ((b["x"] - 7) if left else (b["x"] + b["width"] + 7)) * SCALE
        tx = max(PHONE_X - 2, min(PHONE_X + VW * SCALE + 2, tx))
        ty = PHONE_Y + (b["y"] + min(b["height"] / 2, 34)) * SCALE
        arrows.append(f'<path d="M{ax:.0f} {ay:.0f} C {(ax + tx) / 2:.0f} {ay:.0f}, {(ax + tx) / 2:.0f} {ty:.0f}, {tx:.0f} {ty:.0f}" />'
                      f'<circle cx="{tx:.0f}" cy="{ty:.0f}" r="7" />')
        labels.append(
            f'<div class="call {"l" if left else "r"}" style="left:{lx}px;top:{b["ly"]:.0f}px">'
            f'<span class="n">{i}</span><span class="t">{b["label"]}</span></div>'
        )
    acc = card["accent"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: Bodoni; src: url('{file_uri(FONTS / "BodoniModa.ttf")}'); }}
@font-face {{ font-family: BodoniI; src: url('{file_uri(FONTS / "BodoniModa-Italic.ttf")}'); }}
@font-face {{ font-family: Archivo; src: url('{file_uri(FONTS / "Archivo.ttf")}'); }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ width: 1080px; height: 1350px; background: #fcfaf6; color: #1c1620; font-family: Archivo, sans-serif;
  overflow: hidden; position: relative; }}
.band {{ display: flex; height: 12px; position: absolute; left: 0; right: 0; }} .band i {{ flex: 1; }}
.band.top {{ top: 0; }} .band.bottom {{ bottom: 0; }}
header {{ position: absolute; top: 44px; left: 0; right: 0; text-align: center; }}
.kicker {{ font-weight: 800; letter-spacing: .2em; font-size: 22px; color: {acc}; text-transform: uppercase; }}
h1 {{ font-family: BodoniI; font-weight: 400; font-size: 76px; line-height: 1; margin: 12px 0 10px; }}
.sub {{ font-size: 27px; color: #4a3f4f; }}
.phone {{ position: absolute; left: {PHONE_X - 10}px; top: {PHONE_Y - 10}px; width: {VW * SCALE + 20:.0f}px;
  height: {1350 - PHONE_Y - 92}px; border-radius: 44px 44px 0 0; background: #1c1620; padding: 10px 10px 0;
  overflow: hidden; box-shadow: 0 30px 60px rgba(40,25,10,.25); }}
.phone img {{ display: block; width: {VW * SCALE:.0f}px; border-radius: 34px 34px 0 0; }}
.phone::after {{ content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 120px;
  background: linear-gradient(transparent, #fcfaf6); }}
svg {{ position: absolute; inset: 0; width: 1080px; height: 1350px; pointer-events: none; }}
svg path {{ fill: none; stroke: {acc}; stroke-width: 4; stroke-linecap: round; }}
svg circle {{ fill: {acc}; stroke: #fff; stroke-width: 3; }}
.call {{ position: absolute; width: 266px; display: flex; gap: 10px; align-items: flex-start;
  background: #fff; border: 2px solid {acc}; border-radius: 16px; padding: 10px 12px;
  box-shadow: 0 8px 20px rgba(40,25,10,.12); }}
.call .n {{ flex-shrink: 0; width: 34px; height: 34px; border-radius: 50%; background: {acc}; color: #fff;
  font-weight: 900; font-size: 19px; display: grid; place-items: center; }}
.call .t {{ font-size: 23px; font-weight: 700; line-height: 1.18; }}
footer {{ position: absolute; left: 60px; right: 60px; bottom: 34px; display: flex; align-items: center; gap: 14px; }}
.logo {{ width: 44px; height: 44px; background: #1c1620; -webkit-mask: url('{logo_uri}') center/contain no-repeat; mask: url('{logo_uri}') center/contain no-repeat; }}
footer span {{ font-weight: 900; letter-spacing: .26em; font-size: 20px; }}
footer .url {{ margin-left: auto; letter-spacing: 0; font-size: 24px; color: {acc}; }}
</style></head><body>
<div class="band top">{band}</div>
<header><div class="kicker">{card['kicker']}</div><h1>{card['title']}</h1><p class="sub">{card['sub']}</p></header>
<div class="phone"><img src="{shot_uri}"></div>
<svg>{''.join(arrows)}</svg>
{''.join(labels)}
<footer><div class="logo"></div><span>SWIFTIES CHARTS</span><span class="url">thetsmuseum.app</span></footer>
<div class="band bottom">{band}</div>
</body></html>"""


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        only = set(sys.argv[1:])
        for card in CARDS:
            if only and card["slug"] not in only:
                continue
            ctx = b.new_context(viewport={"width": VW, "height": VH}, device_scale_factor=3, is_mobile=True, has_touch=True)
            if card.get("prebuilt"):
                shot = OUT / f"{card['slug']}_shot.png"
                boxes = json.loads((OUT / f"{card['slug']}_prebuilt.json").read_text(encoding="utf-8"))
            else:
                shot, boxes = capture(card, ctx.new_page())
            ctx.close()
            boxes = layout(boxes)
            (OUT / f"{card['slug']}_boxes.json").write_text(json.dumps(boxes, indent=1), encoding="utf-8")
            html = OUT / f"{card['slug']}.html"
            html.write_text(build_html(card, shot, boxes), encoding="utf-8")
            pg = b.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=2)
            pg.goto(html.as_uri())
            pg.wait_for_timeout(500)
            out = OUT / f"{card['slug']}.png"
            pg.screenshot(path=str(out))
            pg.close()
            img = Image.open(out)
            img.resize((310, round(310 * img.height / img.width)), Image.LANCZOS).save(OUT / f"{card['slug']}_phone.png")
            print(out, [bx["label"] for bx in boxes])
        b.close()


if __name__ == "__main__":
    main()
