import json

with open('website/site/data/swift_top_100.json') as f:
    data = json.load(f)

print("Apple Music Points Display Verification:\n")

for i in range(min(3, len(data['entries']))):
    e = data['entries'][i]
    print(f"Rank {e['rank']}: {e['title']}")
    print(f"  Apple Music TS: {e.get('apple_music_ts_points')} → {e.get('apple_music_ts_points_display')}")
    print(f"  Apple Music GL: {e.get('apple_music_global_points')} → {e.get('apple_music_global_points_display')}")
    print()
