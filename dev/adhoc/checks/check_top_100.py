import json

data = json.load(open('website/site/data/swift_top_100.json'))
print('Top 5 chansons du classement:')
print('=' * 80)
for r in data.get('chart', [])[:5]:
    print(f"Rank {r['rank']}: {r['title']}")
    print(f"  - Streams filtrés: {r.get('weekly_streams', 0):,}")
    print(f"  - Points bonus: {r.get('bonus_points', 0)}")
    print(f"  - Points totaux: {r.get('points', 0)}")
    print()
