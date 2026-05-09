import json

data = json.load(open('website/site/data/swift_top_100.json', encoding='utf-8'))
print("Points breakdown (corrected formula):\n")
print(f"{'Title':<30s} {'Filtered':<12s} {'Unfiltered':<12s} {'Charts Pts':<12s} {'Streams Pts':<12s}")
print("=" * 78)

for r in data.get('entries', []):
    title = r.get('title', '')
    if 'opalite' in title.lower() or 'elizabeth' in title.lower() or 'ophelia' in title.lower():
        filtered = r.get('weekly_streams', 0)  # This is the filtered from chart/Spotify aggregated
        unfiltered = r.get('weekly_unfiltered_streams', 0)  # This is unfiltered from Spotify streams
        charts_pts = r.get('charts_points', 0)
        streams_pts = r.get('streams_points', 0)
        
        print(f"{title:<30s} {filtered:>11,} {unfiltered:>11,} {charts_pts:>11.2f} {streams_pts:>11.2f}")
        
        # Verify calculations
        expected_charts_pts = filtered / 10000 if filtered else 0
        expected_streams_pts = max(0, (unfiltered - filtered) * 0.7 / 10000) if unfiltered else 0
        print(f"  Expected: charts={expected_charts_pts:.2f}, streams={expected_streams_pts:.2f}")
        print()
