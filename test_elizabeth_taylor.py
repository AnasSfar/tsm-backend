import json
from pathlib import Path

# Check current image for Elizabeth Taylor
albums_dir = Path('db/discography/albums')
album_file = albums_dir / 'the_life_of_a_showgirl.json'

data = json.loads(album_file.read_text(encoding='utf-8'))

for section in data.get('sections', []):
    for track in section.get('tracks', []):
        if 'Elizabeth Taylor' in track.get('title', ''):
            print(f"Track: {track.get('title')}")
            print(f"Type: {track.get('type')}")
            print(f"Current image_url: {track.get('image_url', 'MISSING')[:50]}...")
            print()
