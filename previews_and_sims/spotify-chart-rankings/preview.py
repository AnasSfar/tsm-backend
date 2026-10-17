"""Real-data previews of Spotify YTD chart cards. No posts, no source/state writes.

python previews_and_sims/spotify-chart-rankings/preview.py
"""
from pathlib import Path
import html
import json
import sys

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "collectors/spotify/charts/full_charts"))
from generate_ranking_images import generate
from ranking_metrics import TITLES


def write_gallery(out):
    manifest = json.loads((out / "latest-manifest.json").read_text(encoding="utf-8"))
    gallery = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Spotify Charts previews</title><style>body{background:#121212;color:white;font-family:Arial;padding:24px;max-width:1100px;margin:auto}
    img{max-width:100%;width:750px;border-radius:8px}a{color:#1ed760}section{margin-bottom:48px}h1{font-size:32px}
    p{color:#bbb;line-height:1.5}nav{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}figure{margin:16px 0}figcaption{margin:8px 0;color:#bbb}</style></head><body>'''
    gallery += f'<h1>Spotify Charts · Global · {manifest["as_of"]}</h1><p>All Taylor Swift entries and overall top 15 rankings. All days and peaks are within 2026. Click an image to open its full-size PNG.</p><nav>'
    for item in manifest["rankings"]:
        if item["status"] == "ready":
            label = f'{TITLES[item["kind"]]} · {"Taylor" if item["scope"] == "taylor" else "Top 15"}'
            gallery += f'<a href="#{item["kind"]}-{item["scope"]}">{label}</a>'
    gallery += '</nav>'
    for item in manifest["rankings"]:
        label = f'{TITLES[item["kind"]]} · {"Taylor Swift — all entries" if item["scope"] == "taylor" else "Overall top 15"}'
        gallery += f'<section id="{item["kind"]}-{item["scope"]}"><h2>{label}</h2>'
        if item["status"] == "ready":
            gallery += f'<p>{item["entries"]} entries · As of {manifest["as_of"]}</p>'
            for i, name in enumerate(item["images"], 1):
                gallery += f'<figure><a href="{name}"><img loading="lazy" src="{name}" alt="{html.escape(label)} — page {i}"></a><figcaption>Page {i}/{len(item["images"])}</figcaption></figure>'
        else:
            gallery += f'<p>{item["status"]}: {html.escape(item["reason"])}</p>'
        gallery += '</section>'
    (out / "index.html").write_text(gallery + '</body></html>',encoding="utf-8")


def main():
    out = HERE / "latest"
    for old in [*out.glob("*.png"), *out.glob("*.html"), *out.glob("*.json")]:
        old.unlink()  # drop superseded page files (old 4-rows-per-image layout)
    # Same latest date for every family, both scopes. No historical fallback.
    code = generate(scope="both", output_dir=out)
    for path in out.glob("*.png"):
        if path.stem.endswith("_phone"):
            continue
        with Image.open(path) as img:
            phone = path.with_name(path.stem + "_phone.png")
            img.resize((310, round(img.height * 310 / img.width)), Image.Resampling.LANCZOS).save(phone)
        print(f"[PHONE 310px] {phone}")

    write_gallery(out)
    print(f"[GALLERY] {out / 'index.html'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
