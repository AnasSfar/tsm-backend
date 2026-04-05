import csv
from datetime import date, timedelta

week_end = date(2026, 4, 3)
week_start = week_end - timedelta(days=6)
week_dates = set()
for i in range(7):
    d = week_start + timedelta(days=i)
    week_dates.add(d.isoformat())

print('Week dates:', sorted(week_dates))
print()

# Read charts
count = 0
with open('db/charts_history_global.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['date'] in week_dates:
            count += 1
            if count <= 10:
                print(f"Song: {row['song_name']}")
                print(f"  Date: {row['date']}")
                print(f"  Streams: {row['streams']}")
                print()

print(f'Total rows in week: {count}')
