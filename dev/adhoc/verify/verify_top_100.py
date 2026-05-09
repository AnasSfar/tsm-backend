import json

data = json.load(open('website/site/data/swift_top_100.json'))
e = data['entries'][0]
print('Entrée #1:')
print(f'  Title: {e["title"]}')
print(f'  Weekly Streams: {e.get("weekly_streams", 0):,}')
print(f'  Unfiltered Streams: {e.get("weekly_unfiltered_streams", 0):,}')
print(f'  Bonus Points: {e.get("bonus_points", 0)}')
print(f'  Points: {e.get("points", 0):,}')
print(f'  Spotify URL: {str(e.get("spotify_url", "N/A"))[:50]}...')
