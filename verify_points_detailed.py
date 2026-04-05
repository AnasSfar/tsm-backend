import json

with open("website/site/data/swift_top_100.json") as f:
    data = json.load(f)

print("Top 10 entries with all point metrics:\n")
print(f"{'Rank':<5} {'Title':<30} {'Points':<8} {'Charts':<8} {'Streams':<8} {'Bonus':<8}")
print("=" * 80)

for entry in data["entries"][:10]:
    print(f"{entry['rank']:<5} {entry['title']:<30} {entry['points']:<8.2f} {entry['charts_points']:<8.2f} {entry['streams_points']:<8.2f} {entry['bonus_points']:<8.2f}")
