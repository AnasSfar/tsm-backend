"""Render TayBoard methodology cards for social threads."""

from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_INPUT = _REPO_ROOT / "runtime" / "exports" / "web" / "site" / "data" / "swift_top_albums.json"
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "snapshots" / "billboard" / "2026" / "06" / "2026-06-10"
_LOGO_PATH = _REPO_ROOT / "db" / "logo.png"


def _load_payload(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    return obj if isinstance(obj, dict) else {}


def _logo_data_uri() -> str:
    if not _LOGO_PATH.exists():
        return ""
    data = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def _num(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def _example(payload: dict[str, Any]) -> dict[str, Any]:
    entries = payload.get("entries") or []
    entry = entries[0] if entries else {}
    return {
        "title": entry.get("title") or "The Life of a Showgirl",
        "points": entry.get("points_display") or entry.get("points") or "635.6",
        "pct": entry.get("percentage_change"),
        "units": entry.get("units") or "63.56M",
        "spotify": entry.get("units_spotify") or 50291853,
        "spotify_display": _format_units(entry.get("units_spotify") or 50291853),
        "charts": entry.get("units_charts_display") or "28.81M",
        "extra": entry.get("units_surplus_display") or "30.69M",
        "am_ts": entry.get("am_ts_units_display") or "9.79M",
        "am_overall": entry.get("am_global_units_display") or "3.47M",
        "am_total": _format_units(entry.get("units_am") or 13264019),
        "rank": entry.get("rank") or 1,
        "peak": entry.get("peak_position") or 1,
        "woc": entry.get("weeks_on_chart") or 36,
    }


def _format_units(value: Any) -> str:
    try:
        n = float(value)
    except Exception:
        return str(value or "-")
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.2f}k"
    return f"{n:g}"


def _tag(text: str, tone: str = "") -> str:
    return f"<span class='tag {html.escape(tone)}'>{html.escape(text)}</span>"


def _metric(label: str, value: str, tone: str = "", note: str = "") -> str:
    return (
        f"<div class='metric {html.escape(tone)}'>"
        f"<div class='metric-label'>{html.escape(label)}</div>"
        f"<div class='metric-value'>{html.escape(value)}</div>"
        f"<div class='metric-note'>{html.escape(note)}</div>"
        "</div>"
    )


def _formula(label: str, main: str, note: str = "", tone: str = "") -> str:
    return (
        f"<div class='formula {html.escape(tone)}'>"
        f"<div class='formula-label'>{html.escape(label)}</div>"
        f"<div class='formula-main'>{html.escape(main)}</div>"
        f"<div class='formula-note'>{html.escape(note)}</div>"
        "</div>"
    )


def _example_row(ex: dict[str, Any]) -> str:
    pct = ex.get("pct")
    pct_text = "-" if pct is None or pct == "" else f"{pct:+g}%"
    pct_tone = "up" if isinstance(pct, (int, float)) and pct >= 0 else "down"
    return (
        "<table class='example-table'>"
        "<thead><tr><th>#</th><th>Example</th><th>Points</th><th>%</th><th>Units</th></tr></thead>"
        "<tbody><tr>"
        f"<td class='rank'>{html.escape(_num(ex.get('rank')))}</td>"
        f"<td class='example-title'>{html.escape(str(ex.get('title')))}</td>"
        f"<td class='points'>{html.escape(_num(ex.get('points')))}</td>"
        f"<td class='pct {pct_tone}'>{html.escape(pct_text)}</td>"
        f"<td class='units'>{html.escape(_num(ex.get('units')))}</td>"
        "</tr></tbody></table>"
    )


def _card_html(payload: dict[str, Any], index: int, total: int, width: int, height: int) -> str:
    ex = _example(payload)
    week_start = html.escape(str(payload.get("week_start") or ""))
    week_end = html.escape(str(payload.get("week_end") or payload.get("chart_date") or ""))
    logo = _logo_data_uri()
    logo_html = f"<img class='logo' src='{logo}' alt=''/>" if logo else "<div class='logo-fallback'>Swifties Charts</div>"

    if index == 1:
        title = "Introducing TayBoard"
        subtitle = "A weekly ranking system for Taylor songs, albums and eras, based on Spotify + Apple Music activity."
        body = (
            "<div class='tags'>"
            + _tag("Top Songs", "purple")
            + _tag("Top Albums", "purple")
            + _tag("Top Eras", "purple")
            + _tag("Combined / not combined", "purple")
            + "</div>"
            "<div class='grid two'>"
            + _formula("Song-level scoring", "song units = Spotify units + Apple Music units", "Everything starts at track level.")
            + _formula("Then grouped by", "song / album / era / version", "The same units can power multiple TayBoard views.")
            + "</div>"
            + _example_row(ex)
        )
    elif index == 2:
        title = "Spotify units"
        subtitle = "Spotify uses streams in two blocks: chart streams and extra streams outside those chart placements."
        body = (
            _formula("Spotify formula", "chart streams + extra streams x 0.7", "Chart streams count at full value. Extra streams are weighted lower.", "spotify")
            + "<div class='grid three'>"
            + _metric("Chart streams", str(ex["charts"]), "spotify", "Streams while songs appear on Spotify charts.")
            + _metric("Extra streams", str(ex["extra"]), "spotify", "Unfiltered streams minus chart streams.")
            + _metric("Spotify units", str(ex["spotify_display"]), "spotify", "Final Spotify contribution.")
            + "</div>"
            + "<div class='callout'>Example uses the current #1 album to show the scale of each block.</div>"
        )
    elif index == 3:
        title = "Apple Music units"
        subtitle = "Apple Music does not publish stream totals, so TayBoard uses chart positions instead."
        body = (
            "<div class='grid two'>"
            + _metric("TS", str(ex["am_ts"]), "apple", "Taylor Swift Top Songs chart score.")
            + _metric("Overall", str(ex["am_overall"]), "apple", "Global + country chart placements.")
            + "</div>"
            + _formula("Position curve", "higher placement = more units", "#1 carries much more weight than #50, and #50 more than #100.", "apple")
            + _formula("Apple Music total", f"TS + Overall = {ex['am_total']}", "Converted into units and added to Spotify.", "apple")
        )
    elif index == 4:
        title = "From songs to albums, eras and versions"
        subtitle = "Once every song has units, TayBoard can aggregate the same source data into different rankings."
        body = (
            "<div class='grid three'>"
            + _metric("Albums", "sum by album", "units", "All eligible songs from an album are added together.")
            + _metric("Eras", "sum by era", "peak", "Versions and related tracks can roll into an era view.")
            + _metric("Songs", "rank tracks", "purple", "Combined or not combined, depending on the view.")
            + "</div>"
            + _formula(
                "Example album total",
                f"{ex['spotify_display']} Spotify + {ex['am_total']} Apple Music = {ex['units']}",
                f"{ex['title']} earns {ex['points']} points this week.",
            )
        )
    else:
        title = "How to read the table"
        subtitle = "The visible columns are designed to show this week, last week and all-time chart context at once."
        body = (
            "<div class='grid six'>"
            + _metric("+/-", "rank move", "peak", "Change vs last week's rank.")
            + _metric("%", "unit change", "units", "Week-over-week change in total units.")
            + _metric("Peak", f"best #{ex['peak']}", "peak", "Best TayBoard position ever.")
            + _metric("WoC", f"{ex['woc']} weeks", "peak", "Weeks on Chart.")
            + _metric("Points", str(ex["points"]), "purple", "Units scaled down for readability.")
            + _metric("Units", str(ex["units"]), "units", "Final Spotify + Apple Music units.")
            + "</div>"
            + _example_row(ex)
        )

    css = f"""
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      width: {width}px;
      min-height: {height}px;
      background: #f3f4f6;
      color: #111827;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .page {{ width: {width}px; min-height: {height}px; padding: 32px; }}
    .card {{
      min-height: calc({height}px - 64px);
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 22px;
      box-shadow: 0 14px 36px rgba(15,23,42,0.10);
      overflow: hidden;
      position: relative;
    }}
    .bar {{ height: 14px; display: grid; grid-template-columns: 1fr 1fr 1fr; }}
    .bar div:nth-child(1) {{ background: rgba(225,29,72,0.16); }}
    .bar div:nth-child(2) {{ background: rgba(16,185,129,0.16); }}
    .bar div:nth-child(3) {{ background: rgba(139,92,246,0.16); }}
    .inner {{ padding: 30px 40px 72px; }}
    .head {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }}
    .brand {{ display: flex; align-items: center; gap: 14px; }}
    .logo {{ width: 36px; height: 36px; object-fit: contain; }}
    .logo-fallback {{ font-size: 22px; font-weight: 950; color: #d97706; }}
    .kicker {{ font-size: 15px; font-weight: 950; text-transform: uppercase; letter-spacing: 0.04em; }}
    .date {{ font-size: 14px; color: #6b7280; font-variant-numeric: tabular-nums; }}
    h1 {{ margin: 0; font-size: 50px; line-height: 1; font-weight: 950; letter-spacing: 0; }}
    .subtitle {{ margin: 14px 0 22px; font-size: 24px; line-height: 1.25; color: #4b5563; font-weight: 650; max-width: 1320px; }}
    .body {{ display: grid; gap: 16px; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    .tag {{ padding: 10px 14px; border-radius: 999px; background: #f3f4f6; color: #374151; font-weight: 900; font-size: 17px; }}
    .tag.purple {{ background: rgba(139,92,246,0.10); color: #6d28d9; }}
    .grid {{ display: grid; gap: 16px; }}
    .grid.two {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .grid.three {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .grid.six {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .formula, .metric, .callout {{
      border: 1px solid #e5e7eb;
      border-radius: 14px;
      background: #fafafa;
      padding: 18px 20px;
    }}
    .formula.spotify, .metric.spotify {{ background: rgba(16,185,129,0.08); }}
    .formula.apple, .metric.apple {{ background: rgba(225,29,72,0.08); }}
    .metric.units {{ background: rgba(139,92,246,0.08); }}
    .metric.peak {{ background: rgba(245,158,11,0.10); }}
    .metric.purple {{ background: rgba(139,92,246,0.08); }}
    .formula-label, .metric-label {{
      color: #6b7280;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      font-size: 13px;
      font-weight: 950;
      margin-bottom: 8px;
    }}
    .formula-main {{ font-size: 31px; line-height: 1.08; font-weight: 950; font-variant-numeric: tabular-nums; }}
    .formula-note, .metric-note {{ margin-top: 8px; color: #6b7280; font-size: 16px; line-height: 1.28; font-weight: 650; }}
    .metric-value {{ font-size: 30px; line-height: 1; font-weight: 950; font-variant-numeric: tabular-nums; }}
    .callout {{ font-size: 21px; line-height: 1.25; font-weight: 850; color: #374151; }}
    .example-table {{
      width: 100%;
      border-collapse: collapse;
      border-radius: 14px;
      overflow: hidden;
      background: #fff;
      border: 1px solid #e5e7eb;
      font-size: 18px;
    }}
    .example-table th {{
      padding: 13px 15px;
      background: #fafafa;
      color: #6b7280;
      border-bottom: 1px solid #e5e7eb;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-size: 13px;
      text-align: left;
      font-weight: 950;
    }}
    .example-table td {{ padding: 15px; border-bottom: 1px solid #f3f4f6; font-weight: 850; white-space: nowrap; }}
    .example-table .rank {{ width: 62px; text-align: center; font-size: 28px; font-weight: 950; }}
    .example-table .example-title {{ white-space: normal; }}
    .example-table .points {{ color: #7c3aed; text-align: center; }}
    .example-table .pct.up {{ color: #15803d; text-align: center; }}
    .example-table .pct.down {{ color: #b91c1c; text-align: center; }}
    .example-table .units {{ color: #8b5cf6; text-align: right; }}
    .foot {{
      position: absolute; left: 40px; right: 40px; bottom: 22px;
      display: flex; justify-content: space-between; color: #9ca3af;
      font-size: 13px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em;
    }}
    """

    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"/><style>{css}</style></head>
<body>
  <div class="page">
    <section class="card">
      <div class="bar"><div></div><div></div><div></div></div>
      <div class="inner">
        <header class="head">
          <div class="brand">{logo_html}<div class="kicker">TAYBOARD REFERENCE</div></div>
          <div class="date">{week_start} - {week_end}</div>
        </header>
        <h1>{html.escape(title)}</h1>
        <p class="subtitle">{html.escape(subtitle)}</p>
        <div class="body">{body}</div>
      </div>
      <footer class="foot"><span>Spotify + Apple Music methodology</span><span>{index:02d}/{total:02d}</span></footer>
    </section>
  </div>
</body>
</html>"""


def render_cards(input_path: Path, output_dir: Path, width: int, height: int, scale: int) -> None:
    payload = _load_payload(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for index in range(1, 6):
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=scale)
            page.set_content(_card_html(payload, index, 5, width, height), wait_until="load")
            page.wait_for_timeout(100)
            page.screenshot(path=str(output_dir / f"tayboard_reference_{index}.png"), full_page=True)
            page.close()
        browser.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render TayBoard reference cards")
    parser.add_argument("--input", default=str(_DEFAULT_INPUT), help="Path to swift_top_albums JSON")
    parser.add_argument("--output-dir", default=str(_DEFAULT_OUTPUT_DIR), help="Directory for generated PNG files")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--scale", type=int, default=2)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    render_cards(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        width=int(args.width),
        height=int(args.height),
        scale=int(args.scale),
    )


if __name__ == "__main__":
    main()
