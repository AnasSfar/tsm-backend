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
        rows: list[dict[str, Any]],
            page.set_content(html_doc, wait_until="load")
            page.wait_for_timeout(80)

            if idx == 1:
                target = output_path
            else:
                target = output_path.with_name(f"{output_path.stem}_{idx}{output_path.suffix}")

            page.screenshot(path=str(target), full_page=True)

        browser.close()


def load_payload(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render Swift Top 100 to a PNG")
    p.add_argument("--input", default=str(_DEFAULT_INPUT), help="Path to swift_top_100.json")
    p.add_argument("--output", default=str(_DEFAULT_OUTPUT), help="Output PNG path")
    p.add_argument("--columns", type=int, default=1, help="Number of table columns (deprecated)")
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
