import json

data = json.load(open('website/site/data/swift_top_100.json'))
print('Top 10 entries - weekly_unfiltered_streams column:')
print('=' * 100)
for e in data['entries'][:10]:
    print(f"Rank {e['rank']}: {e['title']}")
    filtered = e.get('weekly_streams', 0)
    unfiltered = e.get('weekly_unfiltered_streams', 0)
    print(f"  Filtered (Streams): {filtered:,}")
    print(f"  Unfiltered (Charts): {unfiltered:,}")
    print()
