import json

with open("website/site/data/swift_top_100.json") as f:
    data = json.load(f)

entry = data["entries"][0]
print("Affichage des points avec les champs 'display':\n")
print(f"Rank {entry['rank']}: {entry['title']}")
print(f"  charts_points: {entry['charts_points']} → display: {entry['charts_points_display']}")
print(f"  streams_points: {entry['streams_points']} → display: {entry['streams_points_display']}")
print(f"  bonus_points: {entry['bonus_points']} → display: {entry['bonus_points_display']}")
print(f"  points: {entry['points']} → display: {entry['points_display']}")

print("\n" + "="*60)
print("\nTop 5 with formatted display:\n")
print(f"{'Rank':<5} {'Title':<30} {'Charts':<10} {'Streams':<10} {'Bonus':<8} {'Total':<8}")
print("-" * 75)
for entry in data["entries"][:5]:
    c = entry.get('charts_points_display', '0')
    s = entry.get('streams_points_display', '0')
    b = entry.get('bonus_points_display', '0')
    t = entry.get('points_display', '0')
    print(f"{entry['rank']:<5} {entry['title']:<30} {c:<10} {s:<10} {b:<8} {t:<8}")
