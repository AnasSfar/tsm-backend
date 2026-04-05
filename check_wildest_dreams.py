import json

data = json.load(open('website/site/data/swift_top_100.json'))
print("Looking for 'Wildest Dreams' in top 100:\n")
for e in data['entries']:
    if 'wildest' in e.get('title', '').lower():
        print(f"{e.get('rank'):2}. {e.get('title')}")
        print(f"    Track ID: {e.get('track_id')}")
        print(f"    Image: {e.get('image_url')[:80] if e.get('image_url') else 'None'}...")
        print()
