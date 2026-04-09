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


def build_html(
    *,
    payload: dict[str, Any],
    columns: int,
    limit: int,
    width: int,
) -> str:
    chart_date = str(payload.get("chart_date") or "").strip()
    logo_uri = _tayboard_logo_data_uri()
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
    rows = rows[: max(0, int(limit))]

    columns = max(1, int(columns))
    per_col = (len(rows) + columns - 1) // columns
    chunks = [rows[i : i + per_col] for i in range(0, len(rows), per_col)]

    def _render_table(chunk: list[dict[str, Any]]) -> str:
        out = []
        out.append("<table>")
        out.append(
            "<thead><tr>"
            "<th class='c-rank'>#</th>"
            "<th class='c-delta'>+/-</th>"
            "<th class='c-song'>Song</th>"
            "<th class='c-am'>AM</th>"
            "<th class='c-gl'>GL</th>"
            "<th class='c-units'>Units</th>"
            "<th class='c-points'>Points</th>"
            "<th class='c-pct'>%</th>"
            "<th class='c-peak'>Peak</th>"
            "<th class='c-woc'>WoC</th>"
            "</tr></thead>"
        )
        out.append("<tbody>")
        for r in chunk:
            e = r["e"]
            title = html.escape(str(e.get("title") or ""))
            album = html.escape(str(e.get("primary_album") or ""))
            points = _fmt_int(e.get("points"))
            am = e.get("apple_music_ts_top_songs_best_rank")
            am_s = "—"
            try:
                am_s = f"#{int(am)}" if am is not None and am != "" else "—"
            except Exception:
                am_s = "—"

            gl = e.get("apple_music_global_best_rank")
            gl_s = "—"
            try:
                gl_s = f"#{int(gl)}" if gl is not None and gl != "" else "—"
            except Exception:
                gl_s = "—"
            units_s = html.escape(str(e.get("units") or "—"))
            pct = _fmt_pct(e.get("percentage_change"))
            peak = e.get("peak_position")
            woc = e.get("weeks_on_chart")

            delta, delta_cls = _delta_label(e)

            img = url_to_data_uri(e.get("image_url"))
            img = html.escape(img)

            peak_s = "—"
            peak_is_best = False
            try:
                peak_i = int(peak) if peak is not None and peak != "" else None
                if peak_i is not None:
                    peak_s = f"#{peak_i}"
                    peak_is_best = peak_i == int(r["rank"])
            except Exception:
                peak_s = "—"
                peak_is_best = False

            woc_s = "—"
            try:
                woc_s = str(int(woc)) if woc is not None and woc != "" else "—"
            except Exception:
                woc_s = "—"

            out.append("<tr>")
            out.append(f"<td class='rank'>{r['rank']}</td>")
            out.append(f"<td class='delta {delta_cls}'>{html.escape(delta)}</td>")
            out.append(
                "<td class='song'>"
                "<div class='mini-song'>"
                f"<img src='{img}' alt=''/>"
                "<div class='mini-song-text'>"
                f"<div class='title'>{title}</div>"
                f"<div class='album'>{album}</div>"
                "</div>"
                "</div>"
                "</td>"
            )
            out.append(f"<td class='num am'>{html.escape(am_s)}</td>")
            out.append(f"<td class='num gl'>{html.escape(gl_s)}</td>")
            out.append(f"<td class='num units'>{units_s}</td>")
            out.append(f"<td class='num points'>{points}</td>")
            out.append(f"<td class='num pct'>{html.escape(pct)}</td>")
            peak_cls = "num peak best" if peak_is_best else "num peak"
            out.append(f"<td class='{peak_cls}'>{html.escape(peak_s)}</td>")
            out.append(f"<td class='num woc'>{html.escape(woc_s)}</td>")
            out.append("</tr>")
        out.append("</tbody></table>")
        return "".join(out)

    tables_html = "".join(f"<div class='table-wrap'>{_render_table(c)}</div>" for c in chunks)

    grid_cols = " ".join(["1fr"] * len(chunks))

    css = f"""
    :root {{
      --bg: #ffffff;
      --text: #111111;
      --muted: #6b7280;
      --line: #e5e7eb;
      --line2: #f3f4f6;
      --up: #16a34a;
      --down: #dc2626;
      --new: #0f172a;
    }}

    html, body {{
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Noto Sans", "Liberation Sans", sans-serif;
    }}

    .page {{
      width: {int(width)}px;
      padding: 24px 28px 30px;
      box-sizing: border-box;
    }}

    .head {{
      display: flex;
            align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--line);
      margin-bottom: 14px;
    }}

        .head-title {{
            display: flex;
            align-items: center;
            gap: 10px;
            min-width: 0;
        }}

        .head-title img {{
            width: 24px;
            height: 24px;
            object-fit: contain;
            border-radius: 5px;
            flex: 0 0 auto;
        }}

    .head h1 {{
      margin: 0;
      font-size: 22px;
      letter-spacing: 0.2px;
            line-height: 1.1;
    }}

    .head .sub {{
      margin: 0;
      font-size: 13px;
      color: var(--muted);
      white-space: nowrap;
    }}

    .grid {{
      display: grid;
      grid-template-columns: {grid_cols};
      gap: 18px;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
            font-size: 11.5px;
    }}

    thead th {{
            text-align: center;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
            padding: 7px 7px;
      border-bottom: 1px solid var(--line);
      background: #fafafa;
            vertical-align: middle;
    }}

    tbody td {{
            padding: 5px 7px;
      border-bottom: 1px solid var(--line2);
      vertical-align: middle;
    }}

    td.rank {{
      width: 36px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}

    td.delta {{
      width: 44px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}

    td.delta.up {{ color: var(--up); }}
    td.delta.down {{ color: var(--down); }}
    td.delta.new {{ color: var(--new); }}
    td.delta.flat {{ color: var(--muted); }}

        td.song {{
            width: 300px;
      overflow: hidden;
    }}

    .mini-song {{
      display: flex;
      align-items: center;
            gap: 8px;
      min-width: 0;
    }}

    .mini-song img {{
            width: 28px;
            height: 28px;
            border-radius: 5px;
      object-fit: cover;
      background: #eeeeee;
      flex: 0 0 auto;
    }}

    .mini-song-text {{
      min-width: 0;
    }}

    .title {{
      font-weight: 650;
            line-height: 1.15;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .album {{
            margin-top: 1px;
            font-size: 10px;
      color: var(--muted);
            line-height: 1.1;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

        td.num, th.c-am, th.c-gl, th.c-units, th.c-points, th.c-pct, th.c-peak, th.c-woc {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}

        td.num.points, th.c-points, td.num.units, th.c-units {{
            color: #7c3aed;
            font-weight: 700;
        }}

        td.num.peak.best {{
            background: #fef9c3;
            color: #92400e;
            font-weight: 700;
            border-radius: 6px;
        }}

    th.c-rank {{ width: 36px; }}
    th.c-delta {{ width: 44px; }}
        th.c-song {{ width: 265px; }}
    th.c-am {{ width: 52px; }}
    th.c-gl {{ width: 52px; }}
        th.c-units {{ width: 72px; }}
    th.c-points {{ width: 92px; }}
    th.c-pct {{ width: 58px; }}
    th.c-peak {{ width: 56px; }}
    th.c-woc {{ width: 52px; }}
    """

    title = "Swift Top 100"
    sub = f"Week ending {html.escape(chart_date)}" if chart_date else ""

    logo_html = (
        f"<img src='{html.escape(logo_uri)}' alt=''/>" if logo_uri else ""
    )

    return f"""<!doctype html>
<html>
<head>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<style>{css}</style>
</head>
<body>
  <div class='page'>
    <div class='head'>
            <div class='head-title'>
                {logo_html}
                <h1>{html.escape(title)}</h1>
            </div>
      <p class='sub'>{sub}</p>
    </div>
    <div class='grid'>
      {tables_html}
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
) -> None:
    html_doc = build_html(payload=payload, columns=columns, limit=limit, width=width)

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
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render Swift Top 100 to a PNG")
    p.add_argument("--input", default=str(_DEFAULT_INPUT), help="Path to swift_top_100.json")
    p.add_argument("--output", default=str(_DEFAULT_OUTPUT), help="Output PNG path")
    p.add_argument("--columns", type=int, default=2, help="Number of table columns")
    p.add_argument("--limit", type=int, default=100, help="Number of rows to render")
    p.add_argument("--width", type=int, default=1400, help="Viewport/page width in px")
    p.add_argument("--scale", type=int, default=2, help="Device scale factor")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
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
