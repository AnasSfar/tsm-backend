import json
from pathlib import Path

albums_dir = Path('db/discography/albums')
album_file = albums_dir / 'taylor_swift.json'

print("Testing data parsing...")
data = json.loads(album_file.read_text(encoding='utf-8'))
print(f'Album: {data.get("album")}')

track_count = 0
for section in data.get('sections', []):
    for track in section.get('tracks', []):
        track_count += 1
        if track_count <= 3:
            has_img = bool(track.get('image_url'))
            print(f'  Track {track_count}: {track.get("title", "?")} - img={has_img}')

print(f'Total tracks: {track_count}')
