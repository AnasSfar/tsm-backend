"""Read-only analysis of the supplied X export; source never modified."""
import csv
import json
import math
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE = Path(r'C:\Users\sfara\Downloads\account_analytics_content_2026-09-17_2026-10-14.csv')
OUT = Path(__file__).resolve().parent
TZ = ZoneInfo('Europe/Madrid')
rows = list(csv.DictReader(SOURCE.open(encoding='utf-8-sig', newline='')))
assert len(rows) == len({r['Post id'] for r in rows})
metrics = list(rows[0])[4:]


def category(text):
    t = text.lower()
    if 'rank' in t and 'website' in t and 'you can' in t:
        return 'Site / classement interactif'
    if 'youtube' in t:
        return 'YouTube'
    if 'has now surpassed' in t and ('streams' in t or 'streamed' in t):
        return 'Depassements entre chansons' if 'most streamed' in t else 'Paliers de streams'
    if 'best day' in t or 'biggest day' in t:
        return 'Records de streams'
    if 'apple music' in t:
        return 'Apple Music'
    if 'itunes' in t:
        return 'iTunes'
    if 'spotify counter' in t:
        return 'Recap Spotify'
    if 'eras on spotify' in t:
        return 'Top eras Spotify'
    if 'most streamed songs' in t or 'top 20' in t:
        return 'Top chansons Spotify'
    if 'gainers' in t:
        return 'Gainers Spotify'
    if 'spotify' in t and ('chart' in t or 'charted' in t or 'ranks at' in t):
        return 'Charts Spotify'
    if 'received' in t and 'streams' in t:
        return 'Updates streams albums'
    if 'earned' in t and 'streams' in t:
        return 'Updates streams chansons'
    return 'Autres / a verifier'


for r in rows:
    for m in metrics:
        assert r[m] != ''
        r[m] = int(r[m])
        assert r[m] >= 0
    dt = datetime.fromtimestamp(((int(r['Post id']) >> 22) + 1288834974657) / 1000, timezone.utc)
    assert dt.date() == datetime.strptime(r['Date'], '%a, %b %d, %Y').date()
    r['UTC'] = dt.isoformat()
    local = dt.astimezone(TZ)
    r['Madrid'] = local.isoformat()
    r['jour'] = local.date().isoformat()
    r['heure'] = local.hour
    r['categorie'] = category(r['Post text'])
    r['social'] = sum(r[k] for k in ('Likes', 'Reposts', 'Replies', 'Bookmarks'))
    r['engagement_rate'] = r['Engagements'] / r['Impressions'] if r['Impressions'] else None
    r['social_rate'] = r['social'] / r['Impressions'] if r['Impressions'] else None


def summary(rs):
    total = {m: sum(r[m] for r in rs) for m in metrics}
    return dict(n=len(rs), **total,
                mean_views=st.mean(r['Impressions'] for r in rs),
                median_views=st.median(r['Impressions'] for r in rs),
                median_likes=st.median(r['Likes'] for r in rs),
                engagement_rate=total['Engagements'] / total['Impressions'] if total['Impressions'] else None,
                social_rate=sum(r['social'] for r in rs) / total['Impressions'] if total['Impressions'] else None)


def grouped(rs, key):
    groups = defaultdict(list)
    for r in rs:
        groups[key(r)].append(r)
    return {k: summary(v) for k, v in sorted(groups.items())}


# Full local calendar days wholly inside the UTC export interval.
complete = [r for r in rows if '2026-09-18' <= r['jour'] <= '2026-10-13']
# Exclude the newest publication days from comparative timing/category tables.
mature = [r for r in rows if r['jour'] < '2026-10-12']
eligible = [r for r in mature if r['Impressions'] >= 1000]


def percentile(v, values):
    return (sum(x < v for x in values) + 0.5 * sum(x == v for x in values)) / len(values)


for r in rows:
    r['note_relative_sur_10'] = None
for r in eligible:
    # Equal weight to observed reach and social interaction rate; editorial score, not X's.
    r['note_relative_sur_10'] = round(5 * percentile(r['Impressions'], [x['Impressions'] for x in eligible])
                                        + 5 * percentile(r['social_rate'], [x['social_rate'] for x in eligible]), 2)

daily = grouped(rows, lambda r: r['jour'])
hours = grouped(mature, lambda r: f"{r['heure']//2*2:02}:00-{r['heure']//2*2+2:02}:00")
categories = grouped(mature, lambda r: r['categorie'])
rankings = {}
for m in ('Impressions', 'Likes', 'Engagements', 'Reposts', 'New follows', 'URL Clicks'):
    rankings[m] = sorted(rows, key=lambda r: r[m], reverse=True)[:5]
rankings['social_rate'] = sorted(eligible, key=lambda r: r['social_rate'], reverse=True)[:5]
rankings['note'] = sorted(eligible, key=lambda r: r['note_relative_sur_10'], reverse=True)[:10]
result = dict(source=str(SOURCE), overall=summary(rows), full_days=dict(days=26, **summary(complete)),
              daily=daily, hours=hours, categories=categories, rankings=rankings,
              export_date_average=(len(rows)-sum(r['Date']=='Wed, Oct 14, 2026' for r in rows))/27,
              utc_complete_posts=sum(r['Date']!='Wed, Oct 14, 2026' for r in rows),
              zero_impressions=sum(r['Impressions']==0 for r in rows), mature_n=len(mature), eligible_n=len(eligible))
(OUT/'analytics_results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def write_csv(path, data):
    with path.open('w', encoding='utf-8-sig', newline='') as fh:
        writer=csv.DictWriter(fh, fieldnames=list(data[0]))
        writer.writeheader(); writer.writerows(data)


write_csv(OUT/'posts_analytics_enriched.csv', rows)
write_csv(OUT/'daily_analytics.csv', [dict(date=d, **s) for d,s in daily.items()])
write_csv(OUT/'hourly_analytics.csv', [dict(creneau=d, **s) for d,s in hours.items()])
write_csv(OUT/'category_analytics.csv', [dict(categorie=d, **s) for d,s in categories.items()])
print(json.dumps({k:v for k,v in result.items() if k not in ('rankings','daily')}, ensure_ascii=True, indent=2))
print('DAILY', [(d,s['n']) for d,s in daily.items()])
print('TOPS', json.dumps({k:[{j:r[j] for j in ('Post id','Post text','Impressions','Likes','Engagements','social_rate','Madrid','note_relative_sur_10')} for r in v] for k,v in rankings.items()},ensure_ascii=True))
