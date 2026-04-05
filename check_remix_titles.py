import json

data = json.load(open('website/site/data/swift_top_100.json'))
print("Top 100 - checking for normalized titles:\n")
for e in data['entries']:
    title = e.get('title', '')
    # Check if there are any remix/version markers that should have been removed
    if any(x in title.lower() for x in ['remix', 'version', 'acoustic', 'mix', '[feat']):
        print(f"{e.get('rank'):2}. {title}")
