import json

with open('website/site/data/swift_top_100.json') as f:
    data = json.load(f)

entries = data['entries']
print(f'Total entries: {len(entries)}\n')

# Calculate raw scores and verify
total_units = 0
for e in entries:
    weekly_streams = e['weekly_streams'] or 0
    total_units += weekly_streams

normalization_factor = total_units / 15000
print(f'Total Units (sum of all weekly_streams): {total_units:,.0f}')
print(f'Normalization Factor: {normalization_factor:,.2f}')
print(f'\nTop 10 with energy normalized formula:')

for e in entries[:10]:
    weekly_streams = e['weekly_streams'] or 0
    expected_points = weekly_streams / normalization_factor
    backend_points = e['points']
    print(f"  #{e['rank']:2d} {e['title']:25s} streams={weekly_streams/1e6:5.2f}M points={backend_points:6.1f} (expected={expected_points:6.1f})")
