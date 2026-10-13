"""Landscape (16:9, 1600x900) version of the Data Tour launch post.
Reuses the assets / features of make_post.py. Rendered x2 + a 310px phone copy.

    python previews_and_sims/data-tour-launch-post/make_post_wide.py
"""
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from make_post import BACKEND, BADGE_COLORS, ERA_COLORS, FEATURES, FONTS, FRONT_PUBLIC, data_uri, file_uri

HERE = Path(__file__).resolve().parent


def build_html() -> str:
    keyart = Image.open(FRONT_PUBLIC / "eras-tour-hero" / "landscape-keyart-1200x630.jpg").convert("RGB")
    poster_uri = data_uri(keyart.crop((28, 28, 462, 602)))
    logo_uri = data_uri(Image.open(BACKEND / "db" / "logo.png"), "PNG")
    band = "".join(f'<i style="background:{c}"></i>' for c in ERA_COLORS)

    beads = []
    ci = 0
    for ch in "SWIFTIES CHARTS":
        if ch == " ":
            beads.append(f'<b class="dot" style="background:{ERA_COLORS[ci % 12]}"></b>')
            ci += 1
        else:
            beads.append(f"<b>{ch}</b>")

    tiles = "".join(
        f'<li><span class="n" style="background:{BADGE_COLORS[i]}">{i + 1}</span>'
        f'<div><strong>{t}</strong><em>{d}</em></div></li>'
        for i, (t, d) in enumerate(FEATURES)
    )
    # 8th slot of the 2x4 grid: the site link.
    tiles += '<li class="cta"><div><strong>thetsmuseum.app</strong><em>explore the new site</em></div></li>'

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: Bodoni; src: url('{file_uri(FONTS / "BodoniModa.ttf")}'); }}
@font-face {{ font-family: BodoniI; src: url('{file_uri(FONTS / "BodoniModa-Italic.ttf")}'); }}
@font-face {{ font-family: Archivo; src: url('{file_uri(FONTS / "Archivo.ttf")}'); }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ width: 1600px; height: 900px; background: #fcfaf6; color: #1c1620; font-family: Archivo, sans-serif;
  overflow: hidden; display: flex; flex-direction: column; }}
.band {{ display: flex; height: 12px; flex-shrink: 0; }} .band i {{ flex: 1; }}
main {{ flex: 1; display: grid; grid-template-columns: 560px 1fr; gap: 40px; padding: 34px 48px 26px 56px; min-height: 0; }}
.left {{ display: flex; flex-direction: column; justify-content: center; }}
.hero {{ display: flex; gap: 30px; align-items: center; }}
.poster {{ width: 220px; flex-shrink: 0; transform: rotate(-3deg); border: 8px solid #fff;
  box-shadow: 0 20px 40px rgba(40,25,10,.28); }}
.poster img {{ display: block; width: 100%; }}
.kicker {{ font-weight: 800; letter-spacing: .18em; font-size: 22px; margin-bottom: 14px; color: #8f6115; text-transform: uppercase; }}
h1 {{ font-family: BodoniI; font-weight: 400; font-size: 66px; line-height: 1.02; margin: 30px 0 0; }}
.nw {{ white-space: nowrap; }}
.date {{ display: inline-block; padding: 9px 16px; border-radius: 999px; background: #1c1620; color: #fcfaf6;
  font-weight: 800; font-size: 21px; letter-spacing: .1em; }}
.sub {{ font-size: 27px; line-height: 1.3; color: #4a3f4f; margin-top: 18px; }}
.bracelet {{ display: flex; align-items: center; gap: 4px; margin-top: 28px; position: relative; align-self: flex-start; }}
.bracelet::before {{ content: ""; position: absolute; left: -10px; right: -10px; top: 50%; height: 3px; background: #b9afa2; }}
.bracelet b {{ position: relative; width: 31px; height: 31px; border-radius: 8px; background: #fff; border: 2px solid #e4ddd2;
  display: grid; place-items: center; font-size: 18px; font-weight: 900; box-shadow: 0 3px 6px rgba(0,0,0,.12); }}
.bracelet b.dot {{ width: 16px; height: 16px; border-radius: 50%; border: none; }}
.foot {{ margin-top: 44px; display: flex; align-items: center; gap: 14px; }}
.logo {{ width: 48px; height: 48px; background: #1c1620; -webkit-mask: url('{logo_uri}') center/contain no-repeat; mask: url('{logo_uri}') center/contain no-repeat; }}
.foot span {{ font-weight: 900; letter-spacing: .26em; font-size: 22px; }}
.right {{ display: flex; flex-direction: column; min-height: 0; }}
.new {{ font-family: Bodoni; font-size: 50px; margin-bottom: 18px; }}
.new span {{ font-family: BodoniI; }}
ul {{ list-style: none; flex: 1; display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: repeat(4, 1fr); gap: 14px; }}
li {{ display: flex; align-items: center; gap: 18px; background: #fff; border: 1px solid rgba(28,22,32,.1);
  border-radius: 18px; padding: 0 22px; box-shadow: 0 6px 18px rgba(60,40,20,.07); min-width: 0; }}
.n {{ width: 50px; height: 50px; border-radius: 50%; color: #fff; font-weight: 900; font-size: 26px; flex-shrink: 0;
  display: grid; place-items: center; text-shadow: 0 1px 2px rgba(0,0,0,.35); }}
li strong {{ display: block; font-size: 31px; font-weight: 800; line-height: 1.08; }}
li em {{ display: block; font-style: normal; font-size: 22px; color: #5a4f60; margin-top: 4px; line-height: 1.2; }}
li.cta {{ background: #1c1620; border-color: #1c1620; justify-content: center; text-align: center; }}
li.cta strong {{ color: #f2c46d; }}
li.cta em {{ color: #d8cfc4; }}
</style></head><body>
<div class="band">{band}</div>
<main>
  <section class="left">
    <div class="hero">
      <div class="poster"><img src="{poster_uri}"></div>
      <div>
        <div class="kicker">3 years ago today</div>
        <span class="date">OCT 13 · 2023 → 2026</span>
        <p class="sub">To celebrate, Swifties Charts gets its own <span class="nw">Eras Tour.</span></p>
      </div>
    </div>
    <h1>Happy birthday,<br><span class="nw">Eras Tour film</span></h1>
    <div class="bracelet">{''.join(beads)}</div>
    <div class="foot"><div class="logo"></div><span>SWIFTIES CHARTS</span></div>
  </section>
  <section class="right">
    <div class="new">What's <span>new</span></div>
    <ul>{tiles}</ul>
  </section>
</main>
<div class="band">{band}</div>
</body></html>"""


def main():
    html_path = HERE / "post_wide.html"
    html_path.write_text(build_html(), encoding="utf-8")
    out = HERE / "data_tour_launch_wide.png"
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
        pg.goto(html_path.as_uri())
        pg.wait_for_timeout(500)
        overflow = pg.evaluate("""() => [...document.querySelectorAll('li, .left, h1, .bracelet')]
            .filter(e => e.scrollWidth > e.clientWidth + 1 || e.scrollHeight > e.clientHeight + 1).map(e => e.className || e.tagName)""")
        pg.screenshot(path=str(out))
        b.close()
    img = Image.open(out)
    img.resize((310, round(310 * img.height / img.width)), Image.LANCZOS).save(HERE / "data_tour_launch_wide_phone.png")
    print(out, "overflow:", overflow)


if __name__ == "__main__":
    main()
