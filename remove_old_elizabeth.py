import csv

# Lire le CSV
rows = []
with open('db/streams_history.csv') as f:
    reader = csv.DictReader(f)
    headers = reader.fieldnames
    for row in reader:
        # Supprimer l'ancien track_id d'Elizabeth Taylor
        if row['track_id'] != '1jgTiNob5cVyXeJ3WgX5bL':
            rows.append(row)

print(f"Final count: {len(rows)} rows")

# Écrire le CSV
with open('db/streams_history.csv', 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)

print("Done: removed all old Elizabeth Taylor entries")
