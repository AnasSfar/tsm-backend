import csv

with open('db/swift_top_100_history.csv') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    latest = [r for r in rows if r['date'] == '2026-04-03'][:5]
    for r in latest:
        print(f"#{r['rank']}: {r['title']}")
        print(f"  weekly_streams: {r.get('weekly_streams')}")
        print(f"  bonus_points: {r.get('bonus_points')}")
        print(f"  points: {r.get('points')}")
        print()
