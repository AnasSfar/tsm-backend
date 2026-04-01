import csv
from collections import defaultdict

# Lire l'historique et regarder tous les track_ids
with open('db/streams_history.csv') as f:
    reader = csv.DictReader(f)
    tracks = defaultdict(list)
    for row in reader:
        tid = row['track_id']
        date = row['date']
        tracks[tid].append(date)

# Afficher les tracks qui n'ont qu'une seule entrée (anciens/uniques)
one_off = [(tid, dates) for tid, dates in tracks.items() if len(dates) == 1]
one_off.sort(key=lambda x: x[1][0], reverse=True)
for tid, dates in one_off[-20:]:
    print(f'{tid}: {dates[0]}')
