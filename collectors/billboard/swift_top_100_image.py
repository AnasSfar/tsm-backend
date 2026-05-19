"""Swift Top 100 — PNG generator.

Renders `website/site/data/swift_top_100.json` to a Billboard-style table image.

This uses the same approach as other image generators in this repo:
- fetch covers in Python
- convert to base64 data URIs
- render HTML/CSS in Playwright
- screenshot to PNG

Run:
  python collectors/billboard/swift_top_100_image.py
  python collectors/billboard/swift_top_100_image.py --input website/site/data/swift_top_100.json --output website/site/data/swift_top_100.png
"""

from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_INPUT = _REPO_ROOT / "website" / "site" / "data" / "swift_top_100.json"
_DEFAULT_OUTPUT = _REPO_ROOT / "website" / "site" / "data" / "swift_top_100.png"

_IMG_CACHE: dict[str, str] = {}
_DATA_URI_CACHE: dict[str, str] = {}


def _placeholder_data_uri() -> str:
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'>"
        "<rect width='64' height='64' fill='#e9e9e9'/>"
        "<path d='M16 44 L28 30 L38 40 L46 32 L54 44 Z' fill='#c7c7c7'/>"
        "<circle cx='24' cy='24' r='6' fill='#c7c7c7'/>"
        "</svg>"
    ).encode("utf-8")
    data = base64.b64encode(svg).decode("ascii")
    return f"data:image/svg+xml;base64,{data}"


def url_to_data_uri(url: str | None) -> str:
    if not url:
        return _placeholder_data_uri()
    url = str(url).strip()
    if not url.startswith("http"):
        return _placeholder_data_uri()

    cached = _IMG_CACHE.get(url)
    if cached:
        return cached

    last_exc: Exception | None = None
    for _ in range(2):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as resp:
                mime = getattr(resp.headers, "get_content_type", lambda: None)() or "image/jpeg"
                data = base64.b64encode(resp.read()).decode("ascii")
                result = f"data:{mime};base64,{data}"
            _IMG_CACHE[url] = result
            return result
        except Exception as exc:
            last_exc = exc

    # Fallback to placeholder rather than a broken external URL (file:// Chromium blocks it).
    _IMG_CACHE[url] = _placeholder_data_uri()
    if last_exc:
        pass
    return _IMG_CACHE[url]


def _file_to_data_uri(path: Path) -> str | None:
    key = str(path.resolve())
    cached = _DATA_URI_CACHE.get(key)
    if cached:
        return cached

    try:
        data = path.read_bytes()
    except Exception:
        return None

    suffix = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(suffix, "application/octet-stream")

    data_b64 = base64.b64encode(data).decode("ascii")
    result = f"data:{mime};base64,{data_b64}"
    _DATA_URI_CACHE[key] = result
    return result


def _tayboard_logo_data_uri() -> str | None:
    candidates = [
        _REPO_ROOT.parent / "tsm-frontend" / "frontend" / "public" / "icons" / "billboard.png",
        _REPO_ROOT / "website" / "site" / "icons" / "billboard.png",
        _REPO_ROOT / "icons" / "billboard.png",
        _REPO_ROOT.parent / "tsm-frontend" / "icons" / "billboard.png",
        _REPO_ROOT.parent / "tsm-frontend" / "icons" / "billboard.gif",
    ]
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        uri = _file_to_data_uri(path)
        if uri:
            return uri
    return None


def _fmt_int(value: Any) -> str:
    try:
        n = int(value)
    except Exception:
        return "—"
    return f"{n:,}".replace(",", "\u202f")


def _fmt_pct(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        n = float(value)
    except Exception:
        return "—"
    s = f"{n:+.1f}%" if n else "0.0%"
    return s.replace("+0.0%", "0.0%")


def _delta_label(entry: dict[str, Any]) -> tuple[str, str]:
    prev_rank = entry.get("prev_rank")
    rank_change = entry.get("rank_change")

    if prev_rank is None or prev_rank == "":
        return "NEW", "new"

    try:
        rc = int(rank_change)
    except Exception:
        return "—", "flat"

    if rc > 0:
        return f"+{rc}", "up"
    if rc < 0:
        return str(rc), "down"
    return "0", "flat"


def _sorted_rows(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        entries = payload.get("entries")
        if not isinstance(entries, list):
                return []
        rows: list[dict[str, Any]] = []
        for e in entries:
                if not isinstance(e, dict):
                        continue
                try:
                        rank = int(e.get("rank"))
                except Exception:
                        continue
                rows.append({"rank": rank, "e": e})
        rows.sort(key=lambda r: r["rank"])
        return rows[: max(0, int(limit))]


def build_html(
    *,
    payload: dict[str, Any],
    columns: int,
    limit: int,
    width: int,
    offset: int = 0,
) -> str:
    chart_date = str(payload.get("chart_date") or "").strip()
    week_start = str(payload.get("week_start") or "").strip()
    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = []

    rows: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        try:
            rank = int(e.get("rank"))
        except Exception:
            continue
        rows.append({"rank": rank, "e": e})

    rows.sort(key=lambda r: r["rank"])
    start = max(0, int(offset))
    rows = rows[start : start + max(0, int(limit))]

    columns = max(1, int(columns))
    per_col = (len(rows) + columns - 1) // columns
    chunks = [rows[i : i + per_col] for i in range(0, len(rows), per_col)]

    def _delta_badge(e: dict[str, Any]) -> str:
        change = e.get("change")
        rank_change = e.get("rank_change")
        if change == "NEW":
            return "<span class='badge badge-new'>NEW</span>"
        if change == "RE":
            return "<span class='badge badge-re'>RE</span>"
        try:
            rc = int(rank_change)
        except Exception:
            return "<span class='badge badge-eq'>=</span>"
        if rc == 0:
            return "<span class='badge badge-eq'>=</span>"
        if rc > 0:
            return f"<span class='badge badge-up'>+{rc}</span>"
        return f"<span class='badge badge-down'>{rc}</span>"

    def _render_table(chunk: list[dict[str, Any]]) -> str:
        out = []
        out.append("<table>")
        out.append(
            "<thead>"
            "<tr>"
            "<th rowspan='2' class='c-rank'>Rank</th>"
            "<th rowspan='2' class='c-delta'>+/−</th>"
            "<th rowspan='2' class='c-song'>Song</th>"
            "<th rowspan='2' class='c-points'>Points</th>"
            "<th rowspan='2' class='c-pct'>%</th>"
            "<th rowspan='2' class='c-peak'>Peak</th>"
            "<th rowspan='2' class='c-woc'>WoC</th>"
            "<th colspan='2' class='c-group c-group-am'>Apple Music</th>"
            "<th colspan='2' class='c-group c-group-spotify'>Spotify</th>"
            "<th rowspan='2' class='c-units'>Units</th>"
            "</tr>"
            "<tr>"
            "<th class='c-am'>TS</th>"
            "<th class='c-gl'>Overall</th>"
            "<th class='c-charts'>Charts</th>"
            "<th class='c-streams'>Streams</th>"
            "</tr>"
            "</thead>"
        )
        out.append("<tbody>")
        for r in chunk:
            e = r["e"]
            rank = r["rank"]
            title = html.escape(str(e.get("title") or ""))
            album = html.escape(str(e.get("primary_album") or ""))
            points_s = html.escape(str(e.get("points_display") or _fmt_int(e.get("points"))))

            change = e.get("change")
            pct_val = e.get("percentage_change")
            if change == "NEW":
                pct_s = "NEW"
                pct_cls = "pct-new"
            elif change == "RE":
                pct_s = "RE"
                pct_cls = "pct-re"
            else:
                pct_s = _fmt_pct(pct_val)
                try:
                    pct_cls = "pct-up" if float(pct_val) >= 0 else "pct-down"
                except Exception:
                    pct_cls = ""

            peak = e.get("peak_position")
            times_at_peak = e.get("times_at_peak")
            is_at_peak = peak is not None and peak == rank
            peak_cls = " peak-best" if is_at_peak else ""
            peak_s = "—"
            try:
                if peak is not None and peak != "":
                    tap = ""
                    try:
                        tap_n = int(times_at_peak)
                        if tap_n > 0:
                            tap = f" <span class='times-at-peak'>\u00d7{tap_n}</span>"
                    except Exception:
                        pass
                    peak_s = f"#{int(peak)}{tap}"
            except Exception:
                peak_s = "—"

            woc = e.get("weeks_on_chart")
            woc_s = "—"
            try:
                woc_s = str(int(woc)) if woc is not None and woc != "" else "—"
            except Exception:
                pass

            am_s = html.escape(str(e.get("am_ts_units_display") or "—"))
            gl_s = html.escape(str(e.get("am_global_units_display") or "—"))
            charts_s = html.escape(str(e.get("units_charts_display") or "—"))
            streams_s = html.escape(str(e.get("units_surplus_display") or "—"))
            units_s = html.escape(str(e.get("units") or "—"))

            img = url_to_data_uri(e.get("image_url"))
            img = html.escape(img)

            delta_html = _delta_badge(e)

            out.append("<tr>")
            out.append(f"<td class='td-rank'>{rank}</td>")
            out.append(f"<td class='td-delta'>{delta_html}</td>")
            out.append(
                "<td class='td-song'>"
                "<div class='mini-song'>"
                f"<img src='{img}' alt=''/>"
                "<div class='mini-song-text'>"
                f"<div class='song-title'>{title}</div>"
                f"<div class='song-album'>{album}</div>"
                "</div>"
                "</div>"
                "</td>"
            )
            out.append(f"<td class='td-num td-points'>{points_s}</td>")
            out.append(f"<td class='td-num td-pct {pct_cls}'>{html.escape(pct_s)}</td>")
            out.append(f"<td class='td-num td-peak{peak_cls}'>{peak_s}</td>")
            out.append(f"<td class='td-num td-woc'>{woc_s}</td>")
            out.append(f"<td class='td-num td-am'>{am_s}</td>")
            out.append(f"<td class='td-num td-gl'>{gl_s}</td>")
            out.append(f"<td class='td-num td-charts'>{charts_s}</td>")
            out.append(f"<td class='td-num td-streams'>{streams_s}</td>")
            out.append(f"<td class='td-num td-units'>{units_s}</td>")
            out.append("</tr>")
        out.append("</tbody></table>")
        return "".join(out)

    tables_html = "".join(f"<div class='table-wrap'>{_render_table(c)}</div>" for c in chunks)
    grid_cols = " ".join(["1fr"] * len(chunks))

    if week_start and chart_date:
        sub = f"{html.escape(week_start)} \u2013 {html.escape(chart_date)}"
    elif chart_date:
        sub = f"Week ending {html.escape(chart_date)}"
    else:
        sub = ""

    # Rank range label for multi-image sets
    if rows:
        rank_label = f"#{rows[0]['rank']} \u2013 #{rows[-1]['rank']}"
        sub = f"{sub} &nbsp;·&nbsp; {rank_label}" if sub else rank_label

    logo_uri = _tayboard_logo_data_uri()
    logo_html = f"<img class='head-logo' src='{logo_uri}' alt='TayBoard'/>" if logo_uri else ""

    css = f"""
    html, body {{
      margin: 0;
      padding: 0;
      background: #f8f9fb;
      color: #111111;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Noto Sans", "Liberation Sans", sans-serif;
    }}

    .page {{
      width: {int(width)}px;
      padding: 20px 24px 28px;
      box-sizing: border-box;
    }}

    /* Section card */
    .section-card {{
      background: #ffffff;
      border: 1px solid #e5e7eb;
      border-radius: 20px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.07);
      padding: 18px 20px 20px;
    }}

    /* Header */
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 14px;
    }}

    .head-left {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    .head-logo {{
      width: 28px;
      height: 28px;
      object-fit: contain;
      border-radius: 6px;
    }}

    .section-head h2 {{
      margin: 0;
      font-size: 20px;
      font-weight: 800;
      letter-spacing: -0.01em;
      text-transform: uppercase;
      color: #111111;
    }}

    .section-head .sub {{
      margin: 0;
      font-size: 12px;
      color: #6b7280;
      white-space: nowrap;
    }}

    .grid {{
      display: grid;
      grid-template-columns: {grid_cols};
      gap: 18px;
    }}

    .table-wrap {{
      overflow: hidden;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }}

    /* Header rows */
    thead th {{
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #6b7280;
      padding: 6px 8px;
      border-bottom: 1px solid #e5e7eb;
      background: #fafafa;
      white-space: nowrap;
      text-align: center;
    }}

    /* Group header row */
    .c-group-am {{
      background: rgba(225, 29, 72, 0.10);
      color: #e11d48;
      font-weight: 700;
      font-size: 10px;
      text-align: center;
      letter-spacing: 0.04em;
      border-bottom: none !important;
    }}

    .c-group-spotify {{
      background: rgba(16, 185, 129, 0.10);
      color: #10b981;
      font-weight: 700;
      font-size: 10px;
      text-align: center;
      letter-spacing: 0.04em;
      border-bottom: none !important;
    }}

    /* Sub-header colors */
    .c-points {{ color: #7c3aed; font-weight: 700; }}
    .c-pct    {{ color: #7c3aed; font-weight: 700; }}
    .c-peak   {{ color: #d97706; font-weight: 700; }}
    .c-woc    {{ color: #d97706; font-weight: 700; }}
    .c-am     {{ background: rgba(225, 29, 72, 0.05); color: #e11d48; font-weight: 600; }}
    .c-gl     {{ background: rgba(225, 29, 72, 0.05); color: #e11d48; font-weight: 600; }}
    .c-charts  {{ background: rgba(16, 185, 129, 0.05); color: #10b981; font-weight: 600; }}
    .c-streams {{ background: rgba(16, 185, 129, 0.05); color: #10b981; font-weight: 600; }}
    .c-units   {{ background: rgba(139, 92, 246, 0.05); color: #8b5cf6; font-weight: 600; }}

    /* Body rows */
    tbody td {{
      padding: 5px 8px;
      border-bottom: 1px solid #f3f4f6;
      vertical-align: middle;
    }}

    /* Rank */
    .td-rank {{
      font-size: 18px;
      font-weight: 900;
      letter-spacing: -0.03em;
      text-align: center;
      color: #111111;
      white-space: nowrap;
    }}

    .td-delta {{
      text-align: center;
      white-space: nowrap;
    }}

    /* Badges */
    .badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 34px;
      padding: 3px 7px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.01em;
      white-space: nowrap;
    }}
    .badge-new  {{ background: #dbeafe; color: #1d4ed8; }}
    .badge-re   {{ background: #ede9fe; color: #6d28d9; }}
    .badge-up   {{ background: #dcfce7; color: #15803d; }}
    .badge-down {{ background: #fee2e2; color: #b91c1c; }}
    .badge-eq   {{ background: #f1f5f9; color: #64748b; }}

    /* Song cell */
    .td-song {{ overflow: hidden; }}

    .mini-song {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }}

    .mini-song img {{
      width: 34px;
      height: 34px;
      border-radius: 6px;
      object-fit: cover;
      background: #eeeeee;
      flex: 0 0 auto;
    }}

    .mini-song-text {{ min-width: 0; }}

    .song-title {{
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .song-album {{
      margin-top: 1px;
      font-size: 10.5px;
      color: #6b7280;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    /* Numeric cells */
    .td-num {{
      text-align: center;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }}

    /* Column-specific tints on data cells */
    td.td-points {{ color: #7c3aed !important; font-weight: 600; }}
    .td-pct    {{ font-weight: 600; }}
    .td-am     {{ background: rgba(225, 29, 72, 0.05); color: #e11d48; font-weight: 600; }}
    .td-gl     {{ background: rgba(225, 29, 72, 0.05); color: #e11d48; font-weight: 600; }}
    .td-charts  {{ background: rgba(16, 185, 129, 0.05); color: #10b981; font-weight: 600; }}
    .td-streams {{ background: rgba(16, 185, 129, 0.05); color: #10b981; font-weight: 600; }}
    .td-units   {{ background: rgba(139, 92, 246, 0.05); color: #8b5cf6; font-weight: 600; }}

    /* Peak best highlight */
    .td-peak.peak-best {{
      background: #fef9c3;
      color: #92400e;
      font-weight: 700;
      border-radius: 6px;
    }}

    .times-at-peak {{
      font-size: 10px;
      color: #6b7280;
      font-weight: 500;
    }}

    /* Pct colors */
    .pct-up   {{ color: #15803d; }}
    .pct-down {{ color: #b91c1c; }}
    .pct-new, .pct-re {{ color: #6b7280; }}

    /* Column widths */
    .c-rank    {{ width: 44px; }}
    .c-delta   {{ width: 60px; }}
    .c-song    {{ width: 230px; }}
    .c-points  {{ width: 54px; }}
    .c-pct     {{ width: 50px; }}
    .c-peak    {{ width: 62px; }}
    .c-woc     {{ width: 36px; }}
    .c-am      {{ width: 52px; }}
    .c-gl      {{ width: 56px; }}
    .c-charts  {{ width: 58px; }}
    .c-streams {{ width: 58px; }}
    .c-units   {{ width: 54px; }}
    """

    page_title = str(payload.get("title") or "TayBoard TOP 100")

    return f"""<!doctype html>
<html>
<head>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<style>{css}</style>
</head>
<body>
  <div class='page'>
    <div class='section-card'>
      <div class='section-head'>
        <div class='head-left'>
          {logo_html}
          <h2>{html.escape(page_title)}</h2>
        </div>
        <span class='sub'>{sub}</span>
      </div>
      <div class='grid'>
        {tables_html}
      </div>
    </div>
  </div>
</body>
</html>"""


def render_png(
    *,
    payload: dict[str, Any],
    output_path: Path,
    columns: int = 2,
    limit: int = 100,
    width: int = 1400,
    scale: int = 2,
    offset: int = 0,
) -> None:
    html_doc = build_html(payload=payload, columns=columns, limit=limit, width=width, offset=offset)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Import Playwright only when needed (keeps import-time failures localized).
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": int(width), "height": 900},
            device_scale_factor=int(scale),
        )
        page.set_content(html_doc, wait_until="load")
        page.wait_for_timeout(100)  # tiny settle for layout
        page.screenshot(path=str(output_path), full_page=True)
        browser.close()


def load_payload(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    return obj if isinstance(obj, dict) else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render Swift Top 100 to a PNG")
    p.add_argument("--input", default=str(_DEFAULT_INPUT), help="Path to swift_top_100.json")
    p.add_argument("--output", default=str(_DEFAULT_OUTPUT), help="Output PNG path")
    p.add_argument("--week", type=str, default=None, help="Semaine à générer au format YYYY-MM-DD (remplace --input)")
    p.add_argument("--columns", type=int, default=1, help="Number of table columns (deprecated)")
    p.add_argument("--limit", type=int, default=100, help="Number of rows to render")
    p.add_argument("--width", type=int, default=1400, help="Viewport/page width in px")
    p.add_argument("--scale", type=int, default=2, help="Device scale factor")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Gestion du paramètre --week
    if args.week:
        # On construit le chemin du fichier pour la semaine demandée
        week_str = args.week.strip()
        # Ex: website/site/data/swift_top_100-2026-04-03.json
        week_input = _DEFAULT_INPUT.parent / f"swift_top_100-{week_str}.json"
        in_path = week_input
        # Si l'utilisateur n'a pas spécifié --output, on adapte aussi le nom du PNG
        if args.output == str(_DEFAULT_OUTPUT):
            out_path = _DEFAULT_OUTPUT.parent / f"swift_top_100-{week_str}.png"
        else:
            out_path = Path(args.output)
    else:
        in_path = Path(args.input)
        out_path = Path(args.output)

    payload = load_payload(in_path)
    render_png(
        payload=payload,
        output_path=out_path,
        columns=int(args.columns),
        limit=int(args.limit),
        width=int(args.width),
        scale=int(args.scale),
    )


if __name__ == "__main__":
    main()
