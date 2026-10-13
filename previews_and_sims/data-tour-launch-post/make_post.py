"""One-off X post for the Data Tour launch (2026-10-13): Eras Tour film 3rd
birthday + what's new on the site. Portrait 1080x1350, rendered x2, plus a
310px phone copy to check readability in the mobile timeline.

    python previews_and_sims/data-tour-launch-post/make_post.py
"""
import base64
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
FRONT_PUBLIC = BACKEND.parent / "tsm-frontend" / "frontend" / "public"
FONTS = BACKEND / "collectors" / "comp" / "fonts"

ERA_COLORS = ["#7FB3A0", "#D9B65F", "#8E5BB5", "#B3282D", "#8EC1DE", "#3A3A3A",
              "#F2A7C3", "#9C9C94", "#A0704A", "#2E3A64", "#D8D2C4", "#E0703A"]

BADGE_COLORS = ["#B3282D", "#3A3A3A", "#2E3A64", "#8E5BB5", "#E0703A", "#4F8A73", "#A0704A"]  # dark eras: white digit stays readable

FEATURES = [
    ("A brand-new design", "pick your era, the site dresses up in it"),
    ("Living era themes", "reputation lightning, Showgirl orange & mint…"),
    ("All Artists in Spotify Charts", "every song of the Daily Top 200"),
    ("Album Rankings", "rank every song of every album, era by era"),
    ("Top Collections", "streams ranked by setlist & category"),
    ("Smarter search", "find any song, album or chart faster"),
    ("YouTube", "new releases + official audios next to the videos"),
]


def data_uri(img: Image.Image, fmt="JPEG") -> str:
    buf = io.BytesIO()
    img.save(buf, fmt, quality=92)
    mime = "image/jpeg" if fmt == "JPEG" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


def file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def build_html() -> str:
    keyart = Image.open(FRONT_PUBLIC / "eras-tour-hero" / "landscape-keyart-1200x630.jpg").convert("RGB")
    poster = keyart.crop((28, 28, 462, 602))  # the Eras Tour poster grid
    poster_uri = data_uri(poster)
    logo_uri = data_uri(Image.open(BACKEND / "db" / "logo.png"), "PNG")

    band = "".join(f'<i style="background:{c}"></i>' for c in ERA_COLORS)
    beads_word = "SWIFTIES CHARTS"
    beads = []
    ci = 0
    for ch in beads_word:
        if ch == " ":
            beads.append(f'<b class="dot" style="background:{ERA_COLORS[ci % 12]}"></b>')
            ci += 1
        else:
            beads.append(f"<b>{ch}</b>")
    feats = "".join(
        f'<li><span class="n" style="background:{BADGE_COLORS[i]}">{i + 1}</span>'
        f'<div><strong>{t}</strong><em>{d}</em></div></li>'
        for i, (t, d) in enumerate(FEATURES)
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: Bodoni; src: url('{file_uri(FONTS / "BodoniModa.ttf")}'); }}
@font-face {{ font-family: BodoniI; src: url('{file_uri(FONTS / "BodoniModa-Italic.ttf")}'); }}
@font-face {{ font-family: Archivo; src: url('{file_uri(FONTS / "Archivo.ttf")}'); }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ width: 1080px; height: 1350px; background: #fcfaf6; color: #1c1620;
  font-family: Archivo, sans-serif; overflow: hidden; display: flex; flex-direction: column; }}
.band {{ display: flex; height: 14px; }} .band i {{ flex: 1; }}
.band.bottom {{ margin-top: 16px; }}
.top {{ display: flex; gap: 40px; padding: 34px 56px 0 70px; align-items: center; }}
.poster {{ width: 270px; flex-shrink: 0; transform: rotate(-3deg);
  border: 10px solid #fff; box-shadow: 0 24px 50px rgba(40,25,10,.28); }}
.poster img {{ display: block; width: 100%; }}
.hb {{ flex: 1; }}
.kicker {{ font-weight: 800; letter-spacing: .22em; font-size: 24px; color: #8f6115; text-transform: uppercase; }}
.hb h1 {{ font-family: BodoniI; font-weight: 400; font-size: 70px; line-height: 1.0; margin: 10px 0 10px; }}
.nw {{ white-space: nowrap; }}
.hb p {{ font-size: 29px; line-height: 1.3; color: #4a3f4f; }}
.date {{ display: inline-block; margin-top: 18px; padding: 10px 18px; border-radius: 999px;
  background: #1c1620; color: #fcfaf6; font-weight: 800; font-size: 24px; letter-spacing: .1em; }}
.bracelet {{ display: flex; justify-content: center; align-items: center; gap: 6px; margin: 26px 0 0; position: relative; }}
.bracelet::before {{ content: ""; position: absolute; left: 150px; right: 150px; top: 50%; height: 3px; background: #b9afa2; z-index: 0; }}
.bracelet b {{ position: relative; z-index: 1; width: 38px; height: 38px; border-radius: 9px; background: #fff;
  border: 2px solid #e4ddd2; display: grid; place-items: center; font-size: 22px; font-weight: 900;
  box-shadow: 0 3px 6px rgba(0,0,0,.12); }}
.bracelet b.dot {{ width: 18px; height: 18px; border-radius: 50%; border: none; }}
.new {{ text-align: center; font-family: Bodoni; font-size: 42px; margin: 10px 0 12px; }}
.new span {{ font-family: BodoniI; }}
ul {{ list-style: none; margin: 0 64px; display: grid; gap: 9px; }}
li {{ display: flex; align-items: center; gap: 22px; background: #fff; border: 1px solid rgba(28,22,32,.1);
  border-radius: 16px; padding: 11px 22px; box-shadow: 0 6px 18px rgba(60,40,20,.07); }}
.n {{ width: 46px; height: 46px; border-radius: 50%; color: #fff; font-weight: 900; font-size: 24px;
  display: grid; place-items: center; flex-shrink: 0; text-shadow: 0 1px 2px rgba(0,0,0,.35); }}
li strong {{ display: block; font-size: 32px; font-weight: 800; line-height: 1.1; }}
li em {{ display: block; font-style: normal; font-size: 25px; color: #5a4f60; margin-top: 2px; line-height: 1.15; }}
.foot {{ margin: auto 64px 0; padding-top: 12px; display: flex; align-items: center; gap: 16px; }}
.logo {{ width: 56px; height: 56px; background: #1c1620; -webkit-mask: url('{logo_uri}') center/contain no-repeat; mask: url('{logo_uri}') center/contain no-repeat; }}
.foot .name {{ font-weight: 900; letter-spacing: .26em; font-size: 26px; }}
.foot .url {{ margin-left: auto; font-weight: 800; font-size: 28px; color: #8f6115; }}
</style></head><body>
<div class="band">{band}</div>
<div class="top">
  <div class="poster"><img src="{poster_uri}"></div>
  <div class="hb">
    <div class="kicker">3 years ago today</div>
    <h1>Happy birthday,<br><span class="nw">Eras Tour film</span></h1>
    <p>To celebrate, Swifties Charts gets its own <span class="nw">Eras Tour.</span></p>
    <span class="date">OCT 13 · 2023 → 2026</span>
  </div>
</div>
<div class="bracelet">{''.join(beads)}</div>
<div class="new">What's <span>new</span></div>
<ul>{feats}</ul>
<div class="foot"><div class="logo"></div><span class="name">SWIFTIES CHARTS</span><span class="url">thetsmuseum.app</span></div>
<div class="band bottom">{band}</div>
</body></html>"""


def main():
    html_path = HERE / "post.html"
    html_path.write_text(build_html(), encoding="utf-8")
    out = HERE / "data_tour_launch.png"
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=2)
        pg.goto(html_path.as_uri())
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(out))
        b.close()
    img = Image.open(out)
    img.resize((310, round(310 * img.height / img.width)), Image.LANCZOS).save(HERE / "data_tour_launch_phone.png")
    print(out)


if __name__ == "__main__":
    main()
