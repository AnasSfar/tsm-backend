import csv

before = 0
with open('db/streams_history.csv') as f:
    before = len(list(csv.reader(f))) - 1  # -1 pour l'header

print(f"Kept {before} rows out of total")
