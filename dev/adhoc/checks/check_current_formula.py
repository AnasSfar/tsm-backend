import csv

with open('db/swift_top_100_history.csv') as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        if i < 2:
            title = row.get('title', '')
            points = float(row.get('points', 0)) if row.get('points') else 0
            unfiltered = int(float(row.get('weekly_unfiltered_streams', 0))) if row.get('weekly_unfiltered_streams') else 0
            filtered = int(float(row.get('weekly_filtered_streams', 0))) if row.get('weekly_filtered_streams') else 0
            bonus = int(float(row.get('bonus_points', 0))) if row.get('bonus_points') else 0
            charts = int(float(row.get('charts_points', 0))) if row.get('charts_points') else 0
            
            print(f"Row {i+1}: {title}")
            print(f"  Points: {points}")
            print(f"  Unfiltered streams: {unfiltered}")
            print(f"  Filtered streams: {filtered}")
            print(f"  Bonus points: {bonus}")
            print(f"  Charts points: {charts}")
            
            # Testing formula: (filtered + bonus) / 10000
            calculated = round((filtered + bonus) / 10000, 2)
            print(f"  (filtered + bonus) / 10K = ({filtered} + {bonus}) / 10000 = {calculated}")
            print(f"  Match: {calculated == points}")
            print()
