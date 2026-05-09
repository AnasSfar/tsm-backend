import csv
import json
from pathlib import Path

# Charger les discographies pour les track_ids actuels
db_dir = Path('db/discography')
current_ids = set()

for album_file in db_dir.glob('albums/*.json'):
    try:
        data = json.loads(album_file.read_text())
        for section in data.get('sections', []):
            for track in section.get('tracks', []):
                url = track.get('url') or track.get('spotify_url') or ''
                if track_id_match := __import__('re').search(r'track/([A-Za-z0-9]+)', url):
                    current_ids.add(track_id_match.group(1))
    except:
        pass

# Charger songs.json
try:
    songs = json.loads((db_dir / 'songs.json').read_text())
    for track in songs:
        url = track.get('url') or track.get('spotify_url') or ''
        if track_id_match := __import__('re').search(r'track/([A-Za-z0-9]+)', url):
            current_ids.add(track_id_match.group(1))
except:
    pass

print(f"Current track IDs: {len(current_ids)}")

# Lire CSV et enlever les anciens IDs
rows = []
with open('db/streams_history.csv') as f:
    reader = csv.DictReader(f)
    headers = reader.fieldnames
    for row in reader:
        tid = row['track_id']
        if tid in current_ids:
            rows.append(row)
        else:
            print(f"Removing old track: {row['date']} - {tid}")

print(f"Kept {len(rows)} rows")

# Écrire le CSV nettoyé
with open('db/streams_history.csv', 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
