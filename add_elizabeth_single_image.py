import json
from pathlib import Path

album_file = Path('db/discography/albums/the_life_of_a_showgirl.json')

print('Adding single_image to Elizabeth Taylor...')
print()

data = json.loads(album_file.read_text(encoding='utf-8'))

for section in data.get('sections', []):
    for track in section.get('tracks', []):
        if track.get('title') == 'Elizabeth Taylor':
            img = 'https://i.scdn.co/image/ab67616d0000b273dc6e4c7774e0c77c210d3a31'
            
            print(f'Track: {track.get("title")}')
            print(f'Adding single_image: {img[:60]}...')
            print()
            
            track['single_image'] = img
            
            album_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            print('✓ Added single_image field and saved!')
