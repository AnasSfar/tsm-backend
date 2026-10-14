"""Facts of the day for the album page card (album_page_card.py, live since 2026-10-13).

Every fact is computed from exact data only (streams history, Top Eras, Spotify
Global chart) and gets a priority. Rare events (a new billion, an anniversary,
a chart entry...) compete with meaningful changes. Select at most two complementary
facts; background facts only serve as a single fallback when no event qualifies.

    album_facts(album, target_date, sections, hist, album_tracks) -> [Fact, ...] best first
"""
from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import generate_album_update_image as g
import generate_albums_image as ga
import best_day_since
import post_priority

REPO = Path(__file__).resolve().parents[5]
GLOBAL_CHART = REPO / "db" / "charts_history_global.csv"
ALWAYS_SHOW = 70        # facts at/above this priority are never trimmed
STEP = 100_000_000      # song milestones every 100M (same as post_stream_milestones)
TV = " (Taylor's Version)"


@dataclass
class Fact:
    key: str
    priority: float
    icon: str
    text: str           # already HTML-escaped, may contain <b>

    topic: str = ""
    track_id: str | None = None
    event: bool = True

    def html(self) -> str:
        return (f'<div class="fact" data-p="{self.priority}"><span class="fi">{self.icon}</span>'
                f'<span class="ft">{self.text}</span></div>')


def esc(v) -> str:
    return html.escape(str(v))


def short(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}".rstrip("0").rstrip(".") + "B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


FEAT_RE = re.compile(r"\s*[\(\[](?:feat\.?|ft\.?|with)\s[^\)\]]*[\)\]]", re.I)


def clean_title(t: dict) -> str:
    return FEAT_RE.sub("", g._shorten_title(t.get("title") or t.get("title_clean") or "")).replace(TV, "").strip()


# ── cached sources ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def history() -> dict:
    return best_day_since.load_history()


@lru_cache(maxsize=8)
def era_ranks(target_date: str) -> dict[str, tuple[int, int | None]]:
    """{era name: (rank today, rank yesterday or None)} — same rows as Top Eras."""
    today, yest, week = ga.load_history(target_date)
    rows = ga.build_album_rows(today, yest, week, ga.load_album_track_map(), ga.load_covers(),
                               target_date=target_date)
    rows = [r for r in rows if not ga._is_unranked_era(r["album"])]
    now = {r["album"]: i for i, r in enumerate(sorted(rows, key=lambda r: -r["daily_streams"]), 1)}
    before = {}
    if all(r.get("yest_daily") is not None for r in rows):
        before = {r["album"]: i for i, r in enumerate(sorted(rows, key=lambda r: -r["yest_daily"]), 1)}
    return {a: (rank, before.get(a)) for a, rank in now.items()}


@lru_cache(maxsize=8)
def global_chart(target_date: str) -> dict[str, int]:
    out: dict[str, int] = {}
    if GLOBAL_CHART.exists():
        with GLOBAL_CHART.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("date") == target_date and (row.get("rank") or "").isdigit():
                    out[row["track_id"]] = int(row["rank"])
    return out


@lru_cache(maxsize=32)
def album_release_date(album: str) -> date | None:
    for path in g.ALBUMS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and (data.get("album") or "").casefold() == album.casefold():
            raw = str(data.get("release_date") or "")[:10]
            try:
                return date.fromisoformat(raw)
            except ValueError:
                return None
    return None


def album_daily_series(tracks: list[dict], target: date, days: int) -> list[int | None]:
    """Album daily for target, target-1, ... (index 0 = target). None = a track without an
    exact daily that day (a track not out yet counts 0)."""
    hist = history()
    by_track = {t["track_id"]: {p.day: p.daily for p in hist.get(t["track_id"]) or []} for t in tracks}
    release = {t["track_id"]: str(t.get("release_date") or "")[:10] for t in tracks}
    out = []
    for i in range(days + 1):
        d = target - timedelta(days=i)
        total = 0
        for t in tracks:
            tid = t["track_id"]
            if release[tid] and release[tid] > d.isoformat():
                continue
            v = by_track[tid].get(d)
            if v is None:
                total = None
                break
            total += v
        out.append(total)
    return out


# ── the 9 facts ─────────────────────────────────────────────────────────────

def f_billion(tracks, hist) -> list[Fact]:
    if any((hist.get(t["track_id"]) or {}).get("streams") is None for t in tracks):
        print("[facts] billion pending: incomplete track totals")
        return []
    over, crossed = [], []
    for t in tracks:
        h = hist.get(t["track_id"]) or {}
        s, d = h.get("streams"), h.get("daily")
        if s is None:
            continue
        if s >= 1_000_000_000:
            over.append(t)
            for b in range(1, s // 1_000_000_000 + 1):
                if d is not None and s - d < b * 1_000_000_000 <= s:
                    crossed.append((t, b))
    facts = [Fact("billion_new", post_priority.song_milestone_importance(b * 1_000_000_000, None), "◆", f"<b>“{esc(clean_title(t))}”</b> passed <b>{b}B</b> streams today", "milestone", t["track_id"])
             for t, b in crossed]
    if len(over) >= 1 and not crossed:
        names = ", ".join(clean_title(t) for t in over)
        facts.append(Fact("billion_club", 30, "◆", f"<b>{len(over)} song{'s' if len(over) > 1 else ''} over 1B</b> · {esc(names)}", "billion_club", event=False))
    return facts


def f_anniversary(album, target: date) -> list[Fact]:
    rel = album_release_date(album)
    if not rel or rel >= target:
        return []
    years = target.year - rel.year - ((target.month, target.day) < (rel.month, rel.day))
    this_year = rel.replace(year=target.year) if not (rel.month == 2 and rel.day == 29) else date(target.year, 3, 1)
    gap = (target - this_year).days
    if years < 1 or not -3 <= gap <= 3:
        return []
    when = "today" if gap == 0 else (f"{-gap} day{'s' if -gap > 1 else ''} from now" if gap < 0 else f"{gap} day{'s' if gap > 1 else ''} ago")
    n = years + (1 if gap < 0 else 0)
    return [Fact("anniversary", 95 if gap == 0 else 40 - 5 * abs(gap), "✦",
                 f"<b>{ordinal(n)} anniversary</b> {when} · released {rel.strftime('%b')} {rel.day}, {rel.year}")]


def f_milestones(tracks, hist) -> list[Fact]:
    facts, near = [], []
    for t in tracks:
        h = hist.get(t["track_id"]) or {}
        s, d = h.get("streams"), h.get("daily")
        if s is None:
            continue
        nxt = (s // STEP + 1) * STEP
        if d is not None and d > 0 and s - d < (s // STEP) * STEP <= s and s >= STEP and (s // STEP) % 10:   # crossed today (not a billion)
            facts.append(Fact("milestone_new", post_priority.song_milestone_importance((s // STEP) * STEP, None), "▲", f"<b>“{esc(clean_title(t))}”</b> passed <b>{short((s // STEP) * STEP)}</b> today", "milestone", t["track_id"]))
            continue
        if d is not None and d > 0 and s // STEP % 10 == 0 and s >= STEP and s - d < (s // STEP) * STEP:
            continue  # the same song already has a billion-crossing fact
        near.append(((nxt - s) / nxt, t, nxt - s, nxt))
    if near:
        fraction, t, gap, nxt = min(near, key=lambda x: x[0])
        if fraction <= 0.01:
            billion = nxt % 1_000_000_000 == 0
            score = 45 + 15 * (1 - fraction / 0.01) + (10 if billion else 0)
            facts.append(Fact("milestone_next", score, "◎",
                              f"<b>“{esc(clean_title(t))}”</b> is {gap:,} streams away from <b>{short(nxt)}</b>",
                              "milestone", t["track_id"]))
    return facts


def f_chart(tracks, target_date) -> list[Fact]:
    chart = global_chart(target_date)
    previous = global_chart((date.fromisoformat(target_date) - timedelta(days=1)).isoformat())
    facts = []
    for t in tracks:
        tid = t["track_id"]
        if tid not in chart:
            continue
        rank, before = chart[tid], previous.get(tid)
        previous_complete = set(previous.values()) == set(range(1, 201))
        new_entry = before is None and previous_complete
        entered_top10 = rank <= 10 and ((before is not None and before > 10) or new_entry)
        gained = before - rank if before is not None else 0
        event = entered_top10 or new_entry or gained >= 5
        score = 85 if entered_top10 else max(40, 70 - rank / 10) if new_entry else min(75, 40 + gained * 2) if event else 25
        move = f" · up {gained} spots" if gained > 0 else ""
        label = "entered Spotify Global top 10" if entered_top10 else f"entered Spotify Global at #{rank}" if new_entry else f"#{rank} on Spotify Global"
        facts.append(Fact("chart", score, "♫", f"<b>“{esc(clean_title(t))}”</b> {label}{move}",
                          "chart", tid, event))
    return facts


def f_streak_and_average(tracks, target: date) -> list[Fact]:
    series = album_daily_series(tracks, target, 31)
    facts = []
    streak = 0
    for i in range(len(series) - 1):
        a, b = series[i], series[i + 1]
        if a is None or b is None or a <= b:
            break
        streak += 1
    if streak >= 3:
        facts.append(Fact("streak", min(80, 45 + 5 * streak), "↗", f"<b>Up {streak} days in a row</b>", "album_growth"))
    past = series[1:31]
    if series[0] is not None and len(past) == 30 and all(v is not None for v in past):
        if series[0] > max(past):
            facts.append(Fact("record30", 85, "★", "<b>Highest daily streams in 31 days</b>", "album_growth"))
        avg = sum(past) / 30
        pct = (series[0] - avg) / avg * 100 if avg else 0
        if pct >= 5:
            facts.append(Fact("avg30", min(80, 35 + pct), "≈",
                              f"<b>+{pct:.1f}%</b> vs its 30-day average", "album_growth"))
    return facts


def f_era_rank(album, target_date) -> list[Fact]:
    era = album.replace(TV, "")
    ranks = era_ranks(target_date)
    if era not in ranks:
        return []
    now, before = ranks[era]
    move = ""
    if before is not None and before != now:
        move = f" · {'▲' if before > now else '▼'}{abs(before - now)} vs yesterday"
    label = "era"   # Top Eras merges a Taylor's Version into its original era
    event = before is not None and before > now
    prio = (85 if now == 1 else min(75, 45 + 5 * (before - now))) if event else 20
    return [Fact("era_rank", prio, "#", f"<b>#{now} {label}</b> on Spotify today{move}", "era_rank", event=event)]


def f_songs_up(tracks, hist, target: date) -> list[Fact]:
    rows = [(t, hist.get(t["track_id"]) or {}) for t in tracks]
    if len(rows) < 2:
        return []
    if any(h.get("change") is None or h.get("daily") is None for _, h in rows):
        print("[facts] songs_up pending: incomplete daily/comparison values for album tracks")
        return []
    up = [(t, h) for t, h in rows if h["change"] > 0]
    if len(up) == len(rows):
        return [Fact("songs_up", 60, "▲", f"<b>All {len(rows)} songs up</b> today", "album_growth")]
    facts = []
    for t, h in up:
        daily, gain = h["daily"], h["change"]
        previous = daily - gain
        if previous <= 0:
            continue
        pct = gain / previous * 100
        points = {p.day: p.daily for p in history().get(t["track_id"], [])}
        past = [points.get(target - timedelta(days=i)) for i in range(1, 31)]
        if any(v is None for v in past):
            print(f"[facts] gain pending: {t['track_id']} lacks complete 30-day history for {target}")
            continue
        if points.get(target) != daily or past[0] != previous:
            print(f"[facts] gain pending: {t['track_id']} card/history mismatch for {target}")
            continue
        avg = sum(past) / 30
        # Require a meaningful relative AND absolute gain, above the recent baseline.
        if pct < 5 or gain < 1_000 or avg <= 0 or daily <= avg:
            continue
        score = min(80, 35 + min(20, pct) + min(15, gain / 1_000) + min(10, (daily / avg - 1) * 20))
        facts.append(Fact("songs_up", score, "▲",
                          f"<b>“{esc(clean_title(t))}”</b> up <b>{gain:,} streams (+{pct:.1f}%)</b> vs yesterday",
                          "song_growth", t["track_id"]))
    return facts


def f_top_share(tracks, hist) -> list[Fact]:
    dailies = [(t, (hist.get(t["track_id"]) or {}).get("daily")) for t in tracks]
    if len(dailies) < 2 or any(d is None for _t, d in dailies):
        return []
    total = sum(d for _t, d in dailies)
    t, d = max(dailies, key=lambda x: x[1])
    if not total:
        return []
    return [Fact("top_share", 15, "◐", f"<b>“{esc(clean_title(t))}”</b> = {d / total * 100:.0f}% of today's streams", "top_share", t["track_id"], False)]


def select_facts(facts: list[Fact]) -> list[Fact]:
    """Editorial selection only: score never changes the underlying statistics."""
    ranked = sorted(facts, key=lambda f: -f.priority)
    events = [f for f in ranked if f.event and f.priority >= 35]
    selected = []
    for fact in events:
        if any((fact.topic and fact.topic == old.topic)
               or (fact.track_id and fact.track_id == old.track_id) for old in selected):
            continue
        selected.append(fact)
        if len(selected) == 2:
            break
    if selected:
        return selected
    # One useful background fact is enough; never pad a meaningful event with filler.
    return [next(f for f in ranked if not f.event and f.priority >= 25)] if any(
        not f.event and f.priority >= 25 for f in ranked) else []


def album_facts(album: str, target_date: str, hist: dict, tracks: list[dict]) -> list[Fact]:
    target = date.fromisoformat(target_date)
    facts: list[Fact] = []
    for build in (lambda: f_billion(tracks, hist), lambda: f_anniversary(album, target),
                  lambda: f_milestones(tracks, hist), lambda: f_chart(tracks, target_date),
                  lambda: f_streak_and_average(tracks, target), lambda: f_era_rank(album, target_date),
                  lambda: f_songs_up(tracks, hist, target), lambda: f_top_share(tracks, hist)):
        facts += build()
    return select_facts(facts)


# Drop the lowest-priority facts (below ALWAYS_SHOW) while the side column is taller than
# the track list; at least one fact stays.
TRIM_JS = """() => {
  const side = document.querySelector('.side');
  if (!side) return;
  const lists = [...document.querySelectorAll('.list')];
  [side, ...lists].forEach(el => { el.style.alignSelf = 'start'; });
  const listH = () => Math.max(...lists.map(l => l.offsetHeight));
  let facts = [...side.querySelectorAll('.fact')];
  while (side.offsetHeight > listH() && facts.length > 1) {
    const low = facts.reduce((a, b) => (+b.dataset.p < +a.dataset.p ? b : a));
    if (+low.dataset.p >= %d) break;
    low.remove();
    facts = facts.filter(f => f !== low);
  }
  [side, ...lists].forEach(el => { el.style.alignSelf = ''; });
}""" % ALWAYS_SHOW
