from __future__ import annotations

import json
import math
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def nan_to_none(v):
    if isinstance(v, float):
        try:
            if math.isnan(v):
                return None
        except Exception:
            pass
    return v


def fmt_streams(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", " ")  # narrow no-break space


def fmt_pct(pct) -> str:
    if pct is None:
        return "--"
    sign = "+" if pct >= 0 else ""
    formatted = f"{sign}{pct:.1f}%"
    if formatted == "-0.0%":
        return "+0.0%"
    return formatted


def pct_cls(pct) -> str:
    if pct is None:
        return "neutral"
    return "pos" if pct >= 0 else "neg"


def get_pct(today, ref):
    if not today or not ref or ref == 0:
        return None
    return (today - ref) / ref * 100


def fmt_num(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", " ")


def fmt_delta(today, yesterday) -> tuple[str, str, str]:
    """Returns (num_text, pct_text, css_class) for a delta between two values."""
    if today is None or yesterday is None or yesterday == 0:
        return "—", "", "neutral"
    delta = today - yesterday
    pct = delta / yesterday * 100
    pct_s = f"{pct:+.1f}%"
    if pct_s == "-0.0%":
        pct_s = "+0.0%"
    if delta > 0:
        return f"+{fmt_num(delta)}", pct_s, "pos"
    elif delta < 0:
        return f"−{fmt_num(abs(delta))}", pct_s, "neg"
    return "=", pct_s, "neutral"
