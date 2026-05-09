import json

data = json.load(open('website/site/data/swift_top_100.json'))
print("Top 10 with combined Spotify + Apple Music ranking:\n")
for e in data['entries'][:10]:
    title = e.get('title', '')
    rank = e.get('rank', '?')
    spotify = e.get('weekly_unfiltered_streams', 0)
    am_ts = e.get('apple_music_ts_points', 0) or 0
    am_global = e.get('apple_music_global_points', 0) or 0
    points = e.get('points', 0)
    points_display = e.get('points_display', '')
    
    am_combined = (am_ts + am_global) * 1000 if (am_ts or am_global) else 0
    total = spotify + am_combined
    expected = round(total / 10000, 2) if total > 0 else 0
    
    print(f"{rank:2}. {title}")
    print(f"    Spotify: {spotify:>11,}")
    if am_ts or am_global:
        print(f"    AM Bonus: {am_combined:>11,.0f} (TS:{am_ts:.0f} + GL:{am_global:.0f})")
    print(f"    Total:   {total:>11,.0f} → {points} pts ({points_display}) [expected: {expected}]")
    print()
