import json

with open("website/site/data/swift_top_100.json") as f:
    data = json.load(f)

found = False
for entry in data["entries"]:
    if "elizabeth" in entry["title"].lower():
        print(f"Rank {entry['rank']}: {entry['title']}")
        print(f"  charts_points: {entry['charts_points']}")
        print(f"  streams_points: {entry['streams_points']}")
        print(f"  weekly_streams: {entry['weekly_streams']}")
        found = True
        break

if not found:
    print("Elizabeth Taylor not found in TOP 100")
    print("\nFirst 5 entries:")
    for entry in data["entries"][:5]:
        print(f"  {entry['rank']}: {entry['title']}")
