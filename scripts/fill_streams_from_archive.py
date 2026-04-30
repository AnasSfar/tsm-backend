"""
Fill missing data in streams_history.csv from the Daily Archive CSV files.
Uses flexible title matching to link track_ids to archive song titles.
"""

import json, re, glob, csv, unicodedata
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent / "db"

# ============================================================
# 1. Build track_id -> title mapping
# ============================================================

def extract_tracks(data):
    tracks = []
    if isinstance(data, list):
        for item in data:
            tracks.extend(extract_tracks(item))
    elif isinstance(data, dict):
        if 'tracks' in data:
            for t in data['tracks']:
                if isinstance(t, dict) and 'title' in t:
                    tracks.append(t)
        for key in ['sections', 'albums']:
            if key in data:
                tracks.extend(extract_tracks(data[key]))
    return tracks


id_to_title = {}

for path in [BASE / 'discography/songs.json'] + list((BASE / 'discography/albums').glob('*.json')):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    for track in extract_tracks(data):
        url = track.get('url', '')
        m = re.search(r'/track/([A-Za-z0-9]+)', url)
        if m:
            id_to_title[m.group(1)] = track.get('title', '')
        for hid in track.get('historical_track_ids', []):
            id_to_title[hid] = track.get('title', '')

with open(BASE / 'swift_top_100_history.csv', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        if row['track_id'] and row['title']:
            id_to_title[row['track_id']] = row['title']

print(f"[1] track_id->title mappings: {len(id_to_title)}")

# ============================================================
# 2. Normalization + flexible title matching
# ============================================================

# Expand archive abbreviations to full forms before normalizing
ARCHIVE_EXPANSIONS = {
    r'\btlpss\b': 'the long pond studio sessions',
    r'\bTV\b': "taylor s version",
    r'\bFTV\b': "from the vault",
    r'\bLDR\b': "lana del rey",
    r'\bMore LDR\b': "more lana del rey",
    r'\bWANEGBT\b': "we are never ever getting back together",
    r'\btlgad\b': "the last great american dynasty",
    r'\bDemo\b': "demo recording",
    r'\bAcoustic\b': "acoustic",
    r'\bfeat\b': "feat",
}

TRACK_EXPANSIONS = {
    r'the long pond studio sessions': 'tlpss',
    r"taylor'?s version": 'tv',
    r'from the vault': 'ftv',
    r'original demo recording': 'demo recording',
}


def normalize(t, expand_archive=False, expand_track=False):
    if expand_archive:
        for pattern, replacement in ARCHIVE_EXPANSIONS.items():
            t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)
    if expand_track:
        for pattern, replacement in TRACK_EXPANSIONS.items():
            t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)
    t = t.lower()
    t = unicodedata.normalize('NFD', t)
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    t = re.sub(r'[^a-z0-9 ]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def make_norm_variants(title, is_archive=False):
    """Return a set of normalized variants for a title."""
    variants = set()
    if is_archive:
        variants.add(normalize(title, expand_archive=True))
        variants.add(normalize(title))
    else:
        variants.add(normalize(title, expand_track=True))
        variants.add(normalize(title))
    return variants


# Build normalized archive title -> original archive title
# archive_norm_to_title: norm -> original title in archive
def parse_space_number(s):
    s = s.replace('\xa0', '').replace(' ', '').strip()
    return int(s.replace(' ', '').replace(',', '')) if s else 0


def parse_archive(filepath, stop_on_empty_block=False):
    """Returns {norm_title: {date: daily_streams}, ...} and {norm_title: original_title}"""
    data = {}
    norm_to_orig = {}
    with open(filepath, encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)
        dates = [h.replace('/', '-') for h in header[1:]]
        consecutive_empty = 0
        for row in reader:
            if not row or not row[0].strip():
                consecutive_empty += 1
                if stop_on_empty_block and consecutive_empty >= 3:
                    break
                continue
            consecutive_empty = 0
            orig_title = row[0].strip()
            title_data = {}
            for i, d in enumerate(dates):
                if i + 1 < len(row) and row[i + 1].strip():
                    try:
                        title_data[d] = parse_space_number(row[i + 1])
                    except ValueError:
                        pass
            for norm in make_norm_variants(orig_title, is_archive=True):
                if norm not in data:
                    data[norm] = title_data
                    norm_to_orig[norm] = orig_title
                else:
                    # Merge, newer archive wins
                    data[norm].update(title_data)
    return data, norm_to_orig


archive_2026, n2o_2026 = parse_archive(BASE / '2026 & 2025 - Daily Archive 2026.csv')
archive_2025, n2o_2025 = parse_archive(BASE / '2026 & 2025 - Копія аркуша Daily Archive 2025.csv', stop_on_empty_block=True)

# Merge: 2026 takes precedence on overlapping dates
merged_archive = {}
for n, dd in archive_2025.items():
    merged_archive.setdefault(n, {}).update(dd)
for n, dd in archive_2026.items():
    merged_archive.setdefault(n, {}).update(dd)

print(f"[2] Merged archive keys: {len(merged_archive)}")

# ============================================================
# 3. Match track_id -> archive norm key
# ============================================================

id_to_archive_key = {}    # track_id -> archive norm key
unmatched_ids = []

for tid, title in id_to_title.items():
    matched = False
    for variant in make_norm_variants(title, is_archive=False):
        if variant in merged_archive:
            id_to_archive_key[tid] = variant
            matched = True
            break
    if not matched:
        unmatched_ids.append((tid, title))

print(f"[3] Tracks matched to archive: {len(id_to_archive_key)} / {len(id_to_title)}")
print(f"[3] Unmatched tracks: {len(unmatched_ids)}")
if unmatched_ids[:5]:
    print("    Examples:", [t for _, t in unmatched_ids[:5]])

# ============================================================
# 4. Load streams_history and find missing rows
# ============================================================

with open(BASE / 'streams_history.csv', encoding='utf-8') as f:
    original_rows = list(csv.DictReader(f))

# Index: track_id -> {date: row_dict}
existing = defaultdict(dict)
for r in original_rows:
    existing[r['track_id']][r['date']] = r

all_dates = sorted(set(r['date'] for r in original_rows))
print(f"[4] Total dates in streams_history: {len(all_dates)} ({all_dates[0]} to {all_dates[-1]})")
print(f"[4] Total existing rows: {len(original_rows)}")

# ============================================================
# 5. Generate new rows for missing dates
# ============================================================

new_rows = []
fill_log = []  # (track_id, title, dates_filled)

for tid in list(existing.keys()):
    if tid not in id_to_archive_key:
        continue
    archive_key = id_to_archive_key[tid]
    archive_dates = merged_archive[archive_key]

    present = set(existing[tid].keys())
    # Fill all archive dates not already present (not limited to existing date range)
    fillable = sorted(d for d in archive_dates if d not in present)
    if not fillable:
        continue

    # Determine cumulative streams by working backwards from earliest known entry
    known_sorted = sorted(existing[tid].keys())
    earliest_known_date = known_sorted[0]
    earliest_streams = int(existing[tid][earliest_known_date]['streams'] or 0)
    earliest_daily = int(existing[tid][earliest_known_date]['daily_streams'] or 0)

    # Build a full date->daily_streams dict from archive
    # We also include known dates to compute cumulative correctly
    daily_lookup = {}
    for d, v in archive_dates.items():
        daily_lookup[d] = v
    for d, r in existing[tid].items():
        existing_daily = int(r['daily_streams'] or 0)
        # Prefer archive over existing when existing daily is 0/missing
        if existing_daily > 0 or d not in daily_lookup:
            daily_lookup[d] = existing_daily

    # Compute cumulative going backwards from earliest known entry
    # streams(d) = streams(d+1) - daily_streams(d+1)
    # Build sorted list of all dates (archive + existing) up to earliest known
    all_relevant_dates = sorted(set(daily_lookup.keys()) | {earliest_known_date})
    earliest_idx = all_relevant_dates.index(earliest_known_date)

    # Build cumulative backwards from earliest known
    cumul = {}
    cumul[earliest_known_date] = earliest_streams

    # Backward: streams[d-1] = streams[d] - daily_streams[d]
    for i in range(earliest_idx - 1, -1, -1):
        d = all_relevant_dates[i]
        d_next = all_relevant_dates[i + 1]
        daily_next = daily_lookup.get(d_next, 0)
        cumul[d] = cumul[d_next] - daily_next
        if cumul[d] < 0:
            cumul[d] = 0  # safety floor

    # Also forward from earliest known (for any gaps after earliest)
    # streams[d+1] = streams[d] + daily_streams[d+1]
    for i in range(earliest_idx + 1, len(all_relevant_dates)):
        d = all_relevant_dates[i]
        d_prev = all_relevant_dates[i - 1]
        if d in cumul:
            continue
        daily_d = daily_lookup.get(d, 0)
        cumul[d] = cumul.get(d_prev, 0) + daily_d

    dates_filled = []
    for d in fillable:
        daily = archive_dates[d]
        streams = cumul.get(d, 0)
        new_rows.append({
            'date': d,
            'track_id': tid,
            'streams': str(streams),
            'daily_streams': str(daily),
        })
        dates_filled.append(d)

    fill_log.append((tid, id_to_title.get(tid, '?'), len(dates_filled)))

fill_log.sort(key=lambda x: -x[2])
print(f"\n[5] New rows generated: {len(new_rows)}")
print(f"[5] Tracks filled: {len(fill_log)}")
print("\nTop 20 tracks by rows added:")
for tid, title, n in fill_log[:20]:
    print(f"  {n:4d} rows: '{title}'")

# ============================================================
# 6. Write updated streams_history.csv
# ============================================================

all_rows = original_rows + new_rows
all_rows.sort(key=lambda r: (r['date'], r['track_id']))

output_path = BASE / 'streams_history.csv'
with open(output_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=['date', 'track_id', 'streams', 'daily_streams'])
    writer.writeheader()
    writer.writerows(all_rows)

print(f"\n[6] Written {len(all_rows)} rows to {output_path}")
print(f"    ({len(new_rows)} new rows added)")
