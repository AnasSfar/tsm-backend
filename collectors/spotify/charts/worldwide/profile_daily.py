#!/usr/bin/env python3
"""
Profiling wrapper pour daily.py.

Lance daily.main() normalement, instrumente les fonctions clés en amont
et affiche un rapport de performance à la fin de l'exécution.

Usage:
    python profile_daily.py [args identiques à daily.py]
    python profile_daily.py 2026-05-30 --no-post
    python profile_daily.py --no-post --no-upload

Dépendance optionnelle (profiling async-aware):
    pip install yappi
"""
from __future__ import annotations

import atexit
import collections
import functools
import io
import pstats
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Stores partagés
# ---------------------------------------------------------------------------
_timings: dict[str, list[float]] = {}
_counters: collections.Counter = collections.Counter()
_t_start = time.perf_counter()
_region_stats: dict[str, dict] = {}


def _timed(name: str, fn):
    """Wrap une fonction synchrone — mesure sa durée wall-clock."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        _timings.setdefault(name, []).append(time.perf_counter() - t0)
        return result
    return wrapper


def _timed_async(name: str, fn):
    """Wrap une coroutine asyncio — mesure sa durée wall-clock."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        result = await fn(*args, **kwargs)
        _timings.setdefault(name, []).append(time.perf_counter() - t0)
        return result
    return wrapper


# ---------------------------------------------------------------------------
# Import daily + monkey-patching AVANT toute exécution
# ---------------------------------------------------------------------------
_here = Path(__file__).parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import daily as _d  # noqa: E402

# --- GlobalPause.trigger : compte les 429 déclenchés ---
_orig_gp_trigger = _d.GlobalPause.trigger

async def _counting_trigger(self, seconds: int):
    _counters["429_total"] += 1
    return await _orig_gp_trigger(self, seconds)

_d.GlobalPause.trigger = _counting_trigger

# --- Fonctions synchrones ---
_d._get_bearer_token_and_regions = _timed("bearer_acquisition", _d._get_bearer_token_and_regions)
_d._get_bearer_from_cookies      = _timed("bearer_from_cookies", _d._get_bearer_from_cookies)
_d.build_track_lookup            = _timed("build_track_lookup",  _d.build_track_lookup)
_d.resolve_track_id              = _timed("resolve_track_id",    _d.resolve_track_id)
_d._parse_ts_entries             = _timed("parse_ts_entries",    _d._parse_ts_entries)
_d.maybe_upload_to_r2            = _timed("maybe_upload_r2",     _d.maybe_upload_to_r2)

# --- Coroutine _run_async : temps wall-clock par phase ---
_d._run_async = _timed_async("run_async", _d._run_async)

# --- Coroutine _fetch_region : temps wall-clock par région ---
_orig_fetch_region = _d._fetch_region

async def _fetch_region_profiled(session, sem, pause, pool, region, chart_date, base_headers):
    t0 = time.perf_counter()
    try:
        reg, rows = await _orig_fetch_region(session, sem, pause, pool, region, chart_date, base_headers)
        _region_stats[region] = {
            "duration": time.perf_counter() - t0,
            "ok": True,
            "rows": len(rows),
        }
        return reg, rows
    except Exception:
        _region_stats[region] = {
            "duration": time.perf_counter() - t0,
            "ok": False,
            "rows": 0,
        }
        raise

_d._fetch_region = _fetch_region_profiled


# ---------------------------------------------------------------------------
# Profiler (yappi si disponible, sinon cProfile)
# ---------------------------------------------------------------------------
_use_yappi = False
try:
    import yappi  # type: ignore
    yappi.set_clock_type("wall")
    yappi.start(builtins=False)
    _use_yappi = True
    print("[PROFILE] yappi activé (profiling async-aware)", flush=True)
except ImportError:
    import cProfile as _cProfile
    _pr = _cProfile.Profile()
    _pr.enable()
    print("[PROFILE] yappi absent — utilisation de cProfile (pip install yappi pour l'async)", flush=True)


# ---------------------------------------------------------------------------
# Rapport final (appelé via atexit, même si main() lève une exception)
# ---------------------------------------------------------------------------
def _fmt(seconds: float) -> str:
    """Formate une durée en 'm ss.ss' ou 'ss.ss'."""
    if seconds >= 60:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m{s:05.2f}s"
    return f"{seconds:7.3f}s"


def _bar(dur: float, total: float, width: int = 20) -> str:
    filled = int(dur / total * width) if total > 0 else 0
    return "█" * filled + "░" * (width - filled)


def _print_report() -> None:
    t_total = time.perf_counter() - _t_start
    W = 68
    SEP = "═" * W

    # ── Haut niveau ──────────────────────────────────────────────────────
    print("\n" + SEP)
    print("  PROFIL HAUT NIVEAU")
    print(SEP)

    rows_hl: list[tuple[str, float]] = []

    for i, d in enumerate(_timings.get("bearer_acquisition", []), 1):
        rows_hl.append((f"bearer acquisition #{i}", d))

    bcf = _timings.get("bearer_from_cookies", [])
    if bcf:
        rows_hl.append((f"bearer_from_cookies (×{len(bcf)})", sum(bcf)))

    btl = _timings.get("build_track_lookup", [])
    if btl:
        rows_hl.append(("build_track_lookup", sum(btl)))

    for i, d in enumerate(_timings.get("run_async", []), 1):
        rows_hl.append((f"Phase {i} async (wall-clock)", d))

    ri = _timings.get("resolve_track_id", [])
    if ri:
        avg_ms = sum(ri) / len(ri) * 1000
        rows_hl.append((f"resolve_track_id (×{len(ri)}, {avg_ms:.3f}ms/appel)", sum(ri)))

    pte = _timings.get("parse_ts_entries", [])
    if pte:
        rows_hl.append((f"parse_ts_entries (×{len(pte)})", sum(pte)))

    r2 = _timings.get("maybe_upload_r2", [])
    if r2:
        rows_hl.append(("maybe_upload_to_r2", sum(r2)))

    rows_hl.append(("TOTAL", t_total))

    label_w = max((len(r[0]) for r in rows_hl), default=20) + 2
    for label, dur in rows_hl:
        print(f"  {label:<{label_w}} {_fmt(dur)}  {_bar(dur, t_total)}")

    # ── Stats 429 / régions ───────────────────────────────────────────────
    print("\n" + SEP)
    print("  STATS 429 / RÉGIONS")
    print(SEP)
    print(f"  429 déclenchés (GlobalPause) : {_counters['429_total']}")

    if _region_stats:
        total_r = len(_region_stats)
        failed  = sum(1 for s in _region_stats.values() if not s["ok"])
        print(f"  régions fetchées             : {total_r}  ({failed} échoués)")

        top5 = sorted(_region_stats.items(), key=lambda x: x[1]["duration"], reverse=True)[:5]
        print("  Top 5 régions les + lentes   :")
        for reg, s in top5:
            status = "OK " if s["ok"] else "ERR"
            print(f"    {reg:>6}  {_fmt(s['duration'])}  [{status}]  {s['rows']} rows")

        all_dur = [s["duration"] for s in _region_stats.values()]
        avg_dur = sum(all_dur) / len(all_dur)
        print(f"  durée moy. par région        : {_fmt(avg_dur)}")

    # ── Profil détaillé ───────────────────────────────────────────────────
    print("\n" + SEP)
    print("  PROFIL DÉTAILLÉ (top 20 fonctions, trié par temps cumulé)")
    print(SEP)

    if _use_yappi:
        import yappi as _yappi  # type: ignore
        _yappi.stop()
        stats = _yappi.get_func_stats()
        stats.sort("ttot", ascending=False)
        buf = io.StringIO()
        stats.print_all(out=buf, columns={
            0: ("name", 55),
            1: ("ncall", 7),
            2: ("tsub", 8),
            3: ("ttot", 8),
            4: ("tavg", 8),
        })
        # Filtrer le bruit stdlib/asyncio, garder les fonctions utilisateur
        noise = (
            "asyncio\\", "asyncio/", "<built-in>",
            "\\Lib\\", "/lib/python", "site-packages",
            "threading.py", "selectors.py", "socket.py",
        )
        user_lines = [
            l for l in buf.getvalue().splitlines()
            if l.strip() and not any(n in l for n in noise)
        ]
        for line in user_lines[:28]:
            print(line)
    else:
        _pr.disable()
        buf = io.StringIO()
        ps = pstats.Stats(_pr, stream=buf).sort_stats("cumulative")
        ps.print_stats(20)
        for line in buf.getvalue().splitlines()[:50]:
            print(line)

    print(SEP + "\n")


atexit.register(_print_report)


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sys.exit(_d.main())
