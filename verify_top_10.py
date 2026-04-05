import json

with open("website/site/data/swift_top_100.json") as f:
    data = json.load(f)

for entry in data["entries"][:10]:
    print(f"Rank {entry['rank']}: {entry['title']}")
    print(f"  charts_points: {entry['charts_points']}, streams_points: {entry['streams_points']}")
    print()
