import json

with open('website/site/data/swift_top_100.json') as f:
    data = json.load(f)

entries = data['entries']
print(f'Total entries: {len(entries)}\n')

# Calculate raw scores with corrected formula
total_units = 0
for e in entries:
    streams_points = e['streams_points'] or 0
    am_ts = e['apple_music_ts_points'] or 0
    am_global = e['apple_music_global_points'] or 0
    raw_units = streams_points + (am_ts + am_global) * 1000
    total_units += raw_units

normalization_factor = total_units / 15000
print(f'Total Units (sum of all raw units): {total_units:,.0f}')
print(f'Normalization Factor: {normalization_factor:,.2f}')
print(f'\nTop 10 with corrected formula:')

for e in entries[:10]:
    streams_points = e['streams_points'] or 0
    am_ts = e['apple_music_ts_points'] or 0
    am_global = e['apple_music_global_points'] or 0
    raw_units = streams_points + (am_ts + am_global) * 1000
    expected_points = raw_units / normalization_factor
    backend_points = e['points']
    print(f"  #{e['rank']:2d} {e['title']:25s} raw={raw_units/1e6:5.2f}M points={backend_points:6.1f} (expected={expected_points:6.1f})")

# Check last song to verify full range
last_e = entries[-1]
streams_points = last_e['streams_points'] or 0
am_ts = last_e['apple_music_ts_points'] or 0
am_global = last_e['apple_music_global_points'] or 0
raw_units = streams_points + (am_ts + am_global) * 1000
expected_points = raw_units / normalization_factor
backend_points = last_e['points']
print(f"\n  #{last_e['rank']:2d} {last_e['title']:25s} raw={raw_units/1e6:5.2f}M points={backend_points:6.1f} (expected={expected_points:6.1f})")
