import csv
with open('db/swift_top_100_history.csv') as f:
    reader = csv.DictReader(f)
    rows = [r for r in reader if r['date'] == '2026-04-03'][:2]
    for r in rows:
        print(f"Rank{r['rank']}")
        print(f"  weekly_streams: {r['weekly_streams']}")
        print(f"  bonus_points: {r['bonus_points']}")
        print(f"  points: {r['points']}")
        print()
