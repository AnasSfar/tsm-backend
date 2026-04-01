import json
from pathlib import Path

album_file = Path('db/discography/albums/the_life_of_a_showgirl.json')

print('Updating Elizabeth Taylor with new track ID and image...')
print()

data = json.loads(album_file.read_text(encoding='utf-8'))

for section in data.get('sections', []):
    for track in section.get('tracks', []):
        if track.get('title') == 'Elizabeth Taylor':
            old_url = track.get('url', '')
            old_img = track.get('image_url', '')
            
            new_url = 'https://open.spotify.com/track/3AKV7Mvo2Mx4tb39iPvPlT'
            new_img = 'https://i.scdn.co/image/ab67616d0000b273dc6e4c7774e0c77c210d3a31'
            
            print(f'Track: {track.get("title")}')
            print()
            print(f'Old URL: {old_url}')
            print(f'New URL: {new_url}')
            print()
            print(f'Old image: {old_img[:60]}...')
            print(f'New image: {new_img[:60]}...')
            print()
            
            track['url'] = new_url
            track['image_url'] = new_img
            
            album_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            print('✓ Updated and saved!')
