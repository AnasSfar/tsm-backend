import json
from pathlib import Path

albums_dir = Path('db/discography/albums')
songs_file = Path('db/discography/songs.json')

print('Checking for Elizabeth Taylor track...')
print()

# Check albums
for album_file in sorted(albums_dir.glob('*.json')):
    try:
        data = json.loads(album_file.read_text(encoding='utf-8'))
        for section in data.get('sections', []):
            for track in section.get('tracks', []):
                title = track.get('title', '')
                if title == 'Elizabeth Taylor':
                    url = track.get('url', '')
                    track_id = url.split('/')[-1] if url else 'N/A'
                    print(f'Found in {album_file.name}:')
                    print(f'  Title: {title}')
                    print(f'  Track ID: {track_id}')
                    print(f'  Type: {track.get("type")}')
                    print(f'  Image: {track.get("image_url", "MISSING")[:60]}...')
                    print()
    except:
        pass

# Check songs.json
if songs_file.exists():
    try:
        songs_data = json.loads(songs_file.read_text(encoding='utf-8'))
        for section in songs_data:
            for track in section.get('tracks', []):
                title = track.get('title', '')
                if title == 'Elizabeth Taylor':
                    url = track.get('url', '')
                    track_id = url.split('/')[-1] if url else 'N/A'
                    print(f'Found in songs.json:')
                    print(f'  Title: {title}')
                    print(f'  Track ID: {track_id}')
                    print(f'  Type: {track.get("type")}')
                    print(f'  Image: {track.get("image_url", "MISSING")[:60]}...')
                    print()
    except:
        pass
