import json

data = json.load(open('website/site/data/swift_top_100.json'))
print('Top 5 songs with Spotify data:')
print('=' * 100)
for e in data['entries'][:5]:
    print(f"  {e['rank']}. {e['title']}")
    streams = e.get('weekly_streams', 0)
    unfiltered = e.get('weekly_unfiltered_streams', 0)
    bonus = e.get('bonus_points', 0)
    points = e.get('points', 0)
    print(f"     Streams: {streams:,} | Unfiltered: {unfiltered:,} | Bonus: {bonus} | Points: {points:,}")
    spotify_url = e.get('spotify_url', 'N/A')[:40]
    print(f"     Spotify: {spotify_url}...")
    print()
