import json

with open('website/site/data/swift_top_100.json') as f:
    data = json.load(f)

entries = data['entries']
print(f'Total entries: {len(entries)}\n')

# Calculate raw scores and verify
total_units = 0
for e in entries:
    spotify_unfiltered = e['weekly_unfiltered_streams']
    am_ts = e['apple_music_ts_points'] or 0
    am_global = e['apple_music_global_points'] or 0
    raw_units = spotify_unfiltered + (am_ts + am_global) * 1000
    total_units += raw_units

normalization_factor = total_units / 15000
print(f'Total Units (sum): {total_units:,.0f}')
print(f'Normalization Factor: {normalization_factor:,.2f}')
print(f'\nPoints Range Check (should be 10-800):')

for e in entries[:10]:
    spotify_unfiltered = e['weekly_unfiltered_streams']
    am_ts = e['apple_music_ts_points'] or 0
    am_global = e['apple_music_global_points'] or 0
    raw_units = spotify_unfiltered + (am_ts + am_global) * 1000
    expected_points = raw_units / normalization_factor
    backend_points = e['points']
    print(f"  #{e['rank']:2d} {e['title']:25s} raw={raw_units/1e6:5.2f}M points={backend_points:6.1f} (expected={expected_points:6.1f})")
