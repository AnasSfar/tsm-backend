import json
with open('website/site/data/swift_top_100.json') as f:
    data = json.load(f)
    
print("Current JSON values:")
for e in data['entries'][:5]:
    print(f"#{e['rank']} {e['title']:20s} weekly_streams={e['weekly_streams']/1e6:5.2f}M points={e['points']:6.1f}")
