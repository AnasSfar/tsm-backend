import json
from pathlib import Path

albums_dir = Path('db/discography/albums')
songs_file = Path('db/discography/songs.json')

singles = []

# Check albums for standalone/alternate tracks
for album_file in sorted(albums_dir.glob('*.json')):
    try:
        data = json.loads(album_file.read_text(encoding='utf-8'))
        for section in data.get('sections', []):
            for track in section.get('tracks', []):
                track_type = track.get('type', 'album')
                if track_type in ('standalone', 'alternate_version'):
                    singles.append({
                        'title': track.get('title'),
                        'type': track_type,
                        'album': album_file.name
                    })
    except:
        pass

# Check songs.json
if songs_file.exists():
    try:
        songs_data = json.loads(songs_file.read_text(encoding='utf-8'))
        for section in songs_data:
            for track in section.get('tracks', []):
                track_type = track.get('type', 'album')
                if track_type in ('standalone', 'alternate_version'):
                    singles.append({
                        'title': track.get('title'),
                        'type': track_type,
                        'album': 'songs.json'
                    })
    except:
        pass

print(f'Found {len(singles)} singles/alternate versions:')
print()
for single in singles[:15]:
    print(f'{single["type"]:20} {single["title"][:50]}')
    print(f'  in {single["album"]}')
    print()
