"""Render high-resolution frozen Album Ranking share cards with Chrome."""

from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = ROOT.parent / "tsm-frontend"
FINAL_DATA = FRONTEND_ROOT / "api" / "data" / "album_ranking_final.py"
OUT_DIR = ROOT / "dev" / "artifacts" / "album-ranking-cards"
WORK_DIR = OUT_DIR / "_render"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
POINTS_BY_RANK = [30, 24, 19, 15, 12, 9, 7, 5, 3, 2, 1, 0]
THEMES = {
    "Taylor Swift": {"bg": "#f2f9f3", "surface": "#fafffb", "surface_2": "#e6f4ea", "text": "#1a3d2b", "muted": "#5a7d6a", "line": "rgba(26, 61, 43, 0.10)", "accent": "#5a9e74"},
    "Fearless": {"bg": "#fffdf5", "surface": "#fffefa", "surface_2": "#fef8e1", "text": "#4d3d1e", "muted": "#8a7040", "line": "rgba(77, 61, 30, 0.10)", "accent": "#d4a017"},
    "Speak Now": {"bg": "#f9f5ff", "surface": "#fcfaff", "surface_2": "#f0e8ff", "text": "#3d1f5e", "muted": "#7a5a90", "line": "rgba(61, 31, 94, 0.10)", "accent": "#8a5bb5"},
    "Red": {"bg": "#fff8f8", "surface": "#fffcfc", "surface_2": "#f9eded", "text": "#3d0a0a", "muted": "#8a4545", "line": "rgba(61, 10, 10, 0.10)", "accent": "#b91c1c"},
    "1989": {"bg": "#f0f9ff", "surface": "#f8fdff", "surface_2": "#e0f3fb", "text": "#1a3545", "muted": "#5a8da8", "line": "rgba(26, 53, 69, 0.10)", "accent": "#4aace7"},
    "reputation": {"bg": "#111111", "surface": "#1e1e1e", "surface_2": "#2a2a2a", "text": "#eeeeee", "muted": "#a3a3a3", "line": "rgba(255, 255, 255, 0.10)", "accent": "#ffffff"},
    "Lover": {"bg": "#fff5f9", "surface": "#fffafd", "surface_2": "#ffe8f3", "text": "#5e2d44", "muted": "#a05075", "line": "rgba(94, 45, 68, 0.10)", "accent": "#e8709a"},
    "folklore": {"bg": "#f4f4f4", "surface": "#fafafa", "surface_2": "#ebebeb", "text": "#2a2a2a", "muted": "#6b6b6b", "line": "rgba(42, 42, 42, 0.10)", "accent": "#6b6b6b"},
    "evermore": {"bg": "#fdfaf7", "surface": "#fffdfa", "surface_2": "#f5ece3", "text": "#3e2723", "muted": "#8a6550", "line": "rgba(62, 39, 35, 0.10)", "accent": "#9b6b3d"},
    "Midnights": {"bg": "#0a0e1a", "surface": "#141b2d", "surface_2": "#20273f", "text": "#e2e8f0", "muted": "#94a3b8", "line": "rgba(255, 255, 255, 0.10)", "accent": "#818cf8"},
    "The Tortured Poets Department": {"bg": "#ffffff", "surface": "#ffffff", "surface_2": "#f5f5f5", "text": "#000000", "muted": "#555555", "line": "rgba(0, 0, 0, 0.14)", "accent": "#000000"},
    "The Life of a Showgirl": {"bg": "#fff4ee", "surface": "#fffaf7", "surface_2": "#ffe3d5", "text": "#4a2012", "muted": "#a75632", "line": "rgba(74, 32, 18, 0.12)", "accent": "#ff6b35"},
}


def read_frozen_data() -> tuple[int, list[dict], dict[str, list[int]]]:
    namespace: dict = {}
    exec(FINAL_DATA.read_text(encoding="utf-8"), namespace)
    return (
        namespace["FINAL_ALBUM_RANKING_TOTAL_RANKINGS"],
        namespace["FINAL_ALBUM_RANKING_LEADERBOARD"],
        namespace["FINAL_ALBUM_RANKING_RANK_STATS"],
    )


def slugify(title: str) -> str:
    return title.lower().replace(" ", "-")


def fmt(value: int | float) -> str:
    return f"{round(value):,}"


def pct(value: int | float, total: int | float) -> str:
    rounded = round((value / total) * 100, 1) if total else 0
    return f"{rounded:g}%"


def cover_url(cover_path: str) -> str:
    cover = FRONTEND_ROOT / "frontend" / "public" / cover_path.removeprefix("/")
    return cover.resolve().as_uri()


def card_html(
    total: int,
    rank: int,
    album: dict,
    values: dict[str, list[float]],
    total_by_rank: list[float],
) -> str:
    cells = []
    for index, value in enumerate(values[album["title"]]):
        cells.append(
            f"""
            <div class="cell">
              <span>Rank #{index + 1}</span>
              <strong>{fmt(value)}</strong>
              <small>{pct(value, total_by_rank[index])}</small>
            </div>"""
        )
    title = html.escape(album["title"])
    theme = THEMES[album["title"]]
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <style>
      * {{ box-sizing: border-box; }}
      :root {{
        --theme-bg: {theme["bg"]};
        --theme-surface: {theme["surface"]};
        --theme-surface-2: {theme["surface_2"]};
        --theme-text: {theme["text"]};
        --theme-muted: {theme["muted"]};
        --theme-line: {theme["line"]};
        --theme-accent: {theme["accent"]};
      }}
      html, body {{ margin: 0; }}
      body {{
        width: 386px;
        min-height: 392px;
        padding: 8px 12px 10px;
        background: var(--theme-bg);
        color: var(--theme-text);
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      .card {{
        width: 362px;
        overflow: hidden;
        border: 1px solid var(--theme-line);
        border-radius: 10px;
        background: var(--theme-surface);
        box-shadow: 0 12px 32px rgba(16, 24, 40, .09);
      }}
      .head {{
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 7px;
        padding: 14px 12px 12px;
        text-align: center;
        background: var(--theme-surface-2);
        border-bottom: 1px solid var(--theme-line);
      }}
      .rank-line {{
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
      }}
      .leaderboard-rank {{
        color: var(--theme-accent);
        font-size: 31px;
        line-height: 1;
        font-weight: 950;
        letter-spacing: 0;
      }}
      .cover {{
        width: 62px;
        height: 62px;
        display: block;
        object-fit: contain;
        border-radius: 9px;
        background: var(--theme-surface);
      }}
      h1 {{
        margin: 0;
        font-size: 15px;
        line-height: 1.2;
        font-weight: 800;
        letter-spacing: 0;
      }}
      .meta {{
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 4px 9px;
        margin: 0;
        color: var(--theme-muted);
        font-size: 11px;
        line-height: 1.3;
        font-weight: 800;
      }}
      .meta b {{ color: var(--theme-text); font-variant-numeric: tabular-nums; }}
      .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); }}
      .cell {{
        min-height: 72px;
        padding: 8px 6px;
        border-right: 1px solid var(--theme-line);
        border-bottom: 1px solid var(--theme-line);
        text-align: center;
      }}
      .cell:nth-child(4n) {{ border-right: 0; }}
      .cell:nth-last-child(-n + 4) {{ border-bottom: 0; }}
      .cell span, .cell small {{
        display: block;
        color: var(--theme-muted);
        font-size: 9px;
        line-height: 1.25;
        font-weight: 800;
      }}
      .cell span {{ letter-spacing: .05em; text-transform: uppercase; }}
      .cell strong {{
        display: block;
        margin: 4px 0 2px;
        color: var(--theme-text);
        font-size: 13px;
        line-height: 1.2;
        font-weight: 900;
        font-variant-numeric: tabular-nums;
      }}
      .foot {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        border-top: 1px solid var(--theme-line);
        padding: 8px 12px 9px;
        background: var(--theme-surface-2);
        color: var(--theme-muted);
        font-size: 10px;
        line-height: 1;
        font-weight: 800;
        letter-spacing: 0;
      }}
    </style>
  </head>
  <body>
    <article class="card">
      <header class="head">
        <div class="rank-line">
          <strong class="leaderboard-rank">#{rank}</strong>
          <img class="cover" src="{cover_url(album["cover"])}">
        </div>
        <h1>{title}</h1>
        <p class="meta">
          <b>{fmt(album["total_points"])} pts</b>
          <b>{fmt(total)} participants</b>
        </p>
      </header>
      <section class="grid">{''.join(cells)}
      </section>
      <footer class="foot">
        <span>https://thetsmuseum.app/</span>
        <span>@tsmuseum13</span>
      </footer>
    </article>
  </body>
</html>
"""


def render() -> None:
    total, leaderboard, stats = read_frozen_data()
    total_by_rank = [sum(counts[index] for counts in stats.values()) for index in range(len(POINTS_BY_RANK))]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    for rank, album in enumerate(leaderboard, start=1):
        html_path = WORK_DIR / f"{slugify(album['title'])}.html"
        png_path = OUT_DIR / f"{slugify(album['title'])}.png"
        html_path.write_text(card_html(total, rank, album, stats, total_by_rank), encoding="utf-8")
        subprocess.run(
            [
                str(CHROME),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--allow-file-access-from-files",
                "--force-device-scale-factor=4",
                "--window-size=386,392",
                f"--screenshot={png_path}",
                html_path.resolve().as_uri(),
            ],
            check=True,
        )


if __name__ == "__main__":
    render()
