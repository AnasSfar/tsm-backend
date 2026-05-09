import csv

with open('db/swift_top_100_history.csv') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    latest = [r for r in rows if r['date'] == '2026-04-03'][:3]
    for r in latest:
        print(f"{r['rank']}: {r['title']}")
        print(f"  weekly_streams: {r.get('weekly_streams')}")
        print(f"  apple_music_ts: {r.get('apple_music_ts_points')}")
        print(f"  apple_music_global: {r.get('apple_music_global_points')}")
        print()
