"""Replay of "The Life of a Showgirl: The Encore" (Sep 24-27 2026) through the
real YouTube collector code, with isolated state.

Nothing real is touched: registry/captures/releases/locks live in ./state,
the collector's CSV/JSON in ./state_collector, images in ./cards, Scheduled
Task (un)registration, the YouTube API, R2, the live projection and X posting
are stubbed. Video rows come read-only from db/youtube_views_history.csv.

A (REAL numbers): Topic release of 2026-09-25 04:01 UTC. Its capture tasks
  never ran in reality (deleted by hand), so the daily run of 09-26 04:05:47
  UTC captures it (1-5 min after each +24h mark); the post tick at last mark
  + 15 min posts ONE audio table.
B (FAKE numbers): the 4 lyric videos of 2026-09-26 00:01 UTC with their +24h
  capture tasks firing on time. Real +24h counts were never measured, so the
  stubbed fetch returns the 09-27 04:05 totals (~28h) — layout only.
C (REAL): same lyric videos without capture tasks (what really happened) ->
  daily run at +28h is out of tolerance -> skipped, nothing posted.
S (FAKE number): a lone new video -> the single card.
T (FAKE timing): the lyric videos pretend to come out WITH the Topic audios
  -> one release, 2-post thread (audio table, then video table).
D (REAL numbers, real collector main()): runs of 09-25 and 09-26 06:05 Paris.
  Checks that uploads published after midnight ET (the activity day's end)
  get no row on 09-24 and that their 09-25 daily_views include the views
  they had before the collector first saw them.

    python previews_and_sims/youtube-first-day/simulate.py [abcstd]
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT))
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from collectors.youtube.core import first_day as fd  # noqa: E402

CSV_PATH = REPO_ROOT / "db" / "youtube_views_history.csv"
STATE = HERE / "state"
CARDS = HERE / "cards"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def reset_state() -> None:
    shutil.rmtree(STATE, ignore_errors=True)
    fd.PENDING_DIR = STATE / "pending"
    fd.CAPTURES_DIR = STATE / "captures"
    fd.RELEASES_DIR = STATE / "releases"
    fd.POSTED_LOCK_DIR = STATE / "first_day_posted"


fd._register_one_off_task = lambda name, when, args, **k: print(f"    [task stub] {name} @ {when.isoformat()} ({args.split()[-1]})") or True
fd.unregister_tasks = lambda names, *a, **k: None

POSTS: list[list[tuple[str, Path]]] = []


def fake_poster(posts, *a, **k) -> bool:
    POSTS.append(posts)
    kind = "thread" if len(posts) > 1 else "post"
    print(f"    [post stub] would publish a {kind} of {len(posts)}")
    return True


fd._default_poster = fake_poster

ROWS = [r for r in csv.DictReader(CSV_PATH.open(encoding="utf-8")) if r["date"] >= "2026-09-22"]
FIRST_DATE: dict[str, str] = {}
for row in ROWS:
    FIRST_DATE.setdefault(row["video_id"], row["date"])


def rows_for(date: str) -> list[dict]:
    return [r for r in ROWS if r["date"] == date]


def daily_run(date: str, now: str) -> None:
    rows = rows_for(date)
    new_rows = [r for r in rows if FIRST_DATE.get(r["video_id"]) == date]
    snapshot = utc(rows[0]["snapshot_at"])
    print(f"\n== daily run: activity {date}, snapshot {snapshot.isoformat()}, {len(new_rows)} new video(s)")
    fd.handle_daily_run(new_rows, rows, snapshot, utc(now))
    print("  tick at end of daily run:")
    fd.run_tick(utc(now), out_root=CARDS)


def tick(now: str) -> None:
    print(f"\n== post task tick at {now}")
    fd.run_tick(utc(now), out_root=CARDS)


def status(now: str) -> None:
    print("\n".join("  " + line for line in fd.status_lines(utc(now))))


def later_fetch(ids):
    later = {r["video_id"]: int(r["total_views"]) for r in rows_for("2026-09-26")}
    return {vid: {"viewCount": later[vid]} for vid in ids if vid in later}


def scenario_a() -> None:
    print("\n########## Scenario A — Topic release 2026-09-25 (REAL numbers)")
    reset_state()
    daily_run("2026-09-24", "2026-09-25T04:05:45Z")
    status("2026-09-25T04:06:00Z")
    daily_run("2026-09-25", "2026-09-26T04:05:55Z")
    tick("2026-09-26T04:21:00Z")


def scenario_b() -> None:
    print("\n########## Scenario B — lyric videos with on-time capture tasks (FAKE ~28h numbers)")
    scenario_a()
    for member in [c for c in fd.load_pending().values() if c.channel == "main"]:
        fd.capture_video_live(member.video_id, member.due, later_fetch)
    tick("2026-09-27T00:17:00Z")


def scenario_c() -> None:
    print("\n########## Scenario C — lyric videos, capture tasks deleted (REAL)")
    scenario_a()
    daily_run("2026-09-26", "2026-09-27T04:05:50Z")


def scenario_s() -> None:
    print("\n########## Scenario S — a lone new video -> single card (FAKE ~28h number)")
    reset_state()
    row = next(r for r in rows_for("2026-09-25") if r["video_id"] == "jfVVXYTZykw")
    fd.register_candidates([row], utc("2026-09-26T04:05:55Z"))
    fd.capture_video_live("jfVVXYTZykw", fd.load_pending()["jfVVXYTZykw"].due, later_fetch)
    tick("2026-09-27T00:17:00Z")


def scenario_t() -> None:
    print("\n########## Scenario T — audios + videos in the same release -> thread (FAKE timing/numbers)")
    reset_state()
    lyric_ids = {"jfVVXYTZykw", "BpR280fXISA", "tZnNLoPKriU", "kXOKQCtttbw"}
    lyric_rows = [
        {**r, "published_at": "2026-09-25T04:00:30Z"}
        for r in rows_for("2026-09-25") if r["video_id"] in lyric_ids
    ]
    rows = rows_for("2026-09-24")
    new_rows = [r for r in rows if FIRST_DATE.get(r["video_id"]) == "2026-09-24"] + lyric_rows
    print("\n== daily run 09-24 (lyric videos injected as published 04:00:30 UTC)")
    fd.handle_daily_run(new_rows, rows, utc(rows[0]["snapshot_at"]), utc("2026-09-25T04:05:45Z"))
    for vid in lyric_ids:
        fd.capture_video_live(vid, fd.load_pending()[vid].due, later_fetch)
    daily_run("2026-09-25", "2026-09-26T04:05:55Z")
    tick("2026-09-26T04:21:00Z")


def scenario_d() -> None:
    print("\n########## Scenario D — collector main(): views before detection count on release day (REAL)")
    import collectors.youtube.update_youtube as uy
    from collectors.youtube.core.config import TOPIC_UPLOADS_PLAYLIST_ID

    reset_state()
    sim = HERE / "state_collector"
    shutil.rmtree(sim, ignore_errors=True)
    sim.mkdir(parents=True)
    fieldnames = list(ROWS[0].keys())
    base = rows_for("2026-09-23")
    with (sim / "views.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(base)
    (sim / "last_views.json").write_text(json.dumps({r["video_id"]: int(r["total_views"]) for r in base}), encoding="utf-8")
    real_db = json.loads((REPO_ROOT / "collectors/youtube/tools/json/video_db.json").read_text(encoding="utf-8"))
    known = {r["video_id"] for r in base}
    (sim / "video_db.json").write_text(json.dumps({k: v for k, v in real_db.items() if k in known}), encoding="utf-8")

    uy.CSV_PATH = sim / "views.csv"
    uy.HISTORY_PATH = sim / "last_views.json"
    uy.VIDEO_DB_PATH = sim / "video_db.json"
    uy.TITLE_HISTORY_PATH = sim / "titles.csv"
    uy.YOUTUBE_API_KEY = "sim"
    uy.maybe_upload_youtube_to_r2 = lambda *a, **k: None
    sys.modules["live_trigger"] = types.SimpleNamespace(trigger_live_projection=lambda **k: None)

    def run(activity_date: str, run_date: str, stats_date: str) -> None:
        stats_rows = {r["video_id"]: r for r in rows_for(stats_date)}
        db = json.loads(uy.VIDEO_DB_PATH.read_text(encoding="utf-8"))
        new = [
            {"video_id": vid, "title": r["title"], "published_at": r["published_at"][:10],
             "channel_id": "UCPC0L1d253x-KuMNwa05TpA" if r["channel"] == "topic" else "UCqECaJ8Gagnn7YCbPEzWH6g"}
            for vid, r in stats_rows.items() if vid not in db
        ]

        def discover(api_key, existing, playlist_id=None):
            wanted = "topic" if playlist_id == TOPIC_UPLOADS_PLAYLIST_ID else "main"
            return [v for v in new if v["video_id"] not in existing
                    and stats_rows[v["video_id"]]["channel"] == wanted]

        def fetch(api_key, ids):
            out = {}
            for vid in ids:
                r = stats_rows.get(vid)
                if r:
                    out[vid] = {
                        "title": r["title"], "publishedAt": r["published_at"], "thumbnailUrl": r["thumbnail_url"],
                        "duration": r["duration"], "viewCount": int(r["total_views"]), "likeCount": None,
                        "commentCount": None, "tags": json.loads(r["tags"] or "[]"), "categoryId": "",
                        "liveBroadcastContent": "", "privacyStatus": "", "uploadStatus": "",
                    }
            return out

        uy.discover_new_videos_short_circuit = discover
        uy.fetch_video_stats = fetch
        uy._youtube_collection_date = lambda: run_date
        sys.argv = ["update_youtube", "--no-notify"]
        print(f"\n== collector main(): activity {activity_date} (run {run_date})")
        uy.main()

    run("2026-09-24", "2026-09-25", "2026-09-24")
    run("2026-09-25", "2026-09-26", "2026-09-25")

    out = list(csv.DictReader((sim / "views.csv").open(encoding="utf-8")))
    real = {(r["date"], r["video_id"]): r for r in ROWS}
    print("\n  video          09-24 row (before -> now)       09-25 daily_views (before -> now)")
    for vid in ["V-uIp-WuD60", "cCvLt6gqAIA", "n6Ld4J1tw9U", "9FHuWije2VE"]:
        now24 = next((r["daily_views"] for r in out if r["video_id"] == vid and r["date"] == "2026-09-24"), "no row")
        now25 = next((r["daily_views"] for r in out if r["video_id"] == vid and r["date"] == "2026-09-25"), "no row")
        print(f"  {vid}   {real[('2026-09-24', vid)]['daily_views']:>6} -> {now24:<8}"
              f"            {int(real[('2026-09-25', vid)]['daily_views']):>9,} -> {int(now25):,}")


if __name__ == "__main__":
    shutil.rmtree(CARDS, ignore_errors=True)
    scenario = sys.argv[1] if len(sys.argv) > 1 else "abcstd"
    for name in scenario:
        {"a": scenario_a, "b": scenario_b, "c": scenario_c, "s": scenario_s,
         "t": scenario_t, "d": scenario_d}[name]()
    print(f"\n{len(POSTS)} publication(s) in total:")
    for posts in POSTS:
        print("  - " + " + ".join(str(image.relative_to(HERE)) for _, image in posts))
