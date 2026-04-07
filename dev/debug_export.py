import json

# Read the latest exported JSON
with open('website/site/data/swift_top_100.json') as f:
    payload = json.load(f)

entry = payload['entries'][0]
print(f"Entry rank {entry['rank']}: {entry['title']}")
print()

# Check what fields exist
print("Fields in entry:")
required_fields = ['points', 'points_display', 'charts_points', 'charts_points_display', 
                   'bonus_points', 'bonus_points_display', 'streams_points', 'streams_points_display']

for field in required_fields:
    value = entry.get(field, '❌ MISSING')
    print(f"  {field}: {value}")

print()
print("DEBUG - CSV data for same rank:")
import pandas as pd
df = pd.read_csv('db/swift_top_100_history.csv')
df_latest = df[df['date'] == '2026-04-03'].head(1)
if not df_latest.empty:
    row = df_latest.iloc[0]
    print(f"  CSV points: {row['points']}")
    print(f"  CSV points_display: {row.get('points_display', 'MISSING')}")
