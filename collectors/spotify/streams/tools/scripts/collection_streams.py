"""Separate exact Spotify history for collections of other artists' recordings.

Called only by the normal, current-date daily run after its Spotify update gate.
Never inserted into Taylor's songs DB, artist totals, charts or posting pipeline.
The first observation is a baseline, not a fabricated daily count.
"""
from datetime import date, timedelta
import json
import logging
from pathlib import Path
import os

import requests

ROOT = Path(__file__).resolve().parents[5]
TRACKS = ROOT / 'db/collections/songwriting_tracks.json'
HISTORY = ROOT / 'db/collections/songwriting_history.json'
R2_KEY = 'collections/songwriting/history.json'
logger = logging.getLogger(__name__)


def exact_point(total, previous):
    if type(total) is not int or total < 0:
        return {'s': None, 'd': None, 'reason': 'Spotify did not return an exact total'}
    prior = previous.get('s') if isinstance(previous, dict) else None
    if type(prior) is not int or prior < 0:
        return {'s': total, 'd': None, 'reason': 'Missing previous-calendar-day total; baseline only'}
    if total <= prior:
        return {'s': None, 'd': None, 'reason': f'Unverified unchanged/decreasing Spotify counter: {total}, previous: {prior}'}
    return {'s': total, 'd': total - prior}


def collect_songwriting(stats_date, token_mgr):
    from spotify_api import fetch_playcount_api
    from stream_utils import get_stats_date_str
    from history_store import _r2_config
    if stats_date != get_stats_date_str():
        raise ValueError(f'Refusing current Spotify counters for historical date {stats_date}')
    previous_date = (date.fromisoformat(stats_date) - timedelta(days=1)).isoformat()
    tracks = json.loads(TRACKS.read_text(encoding='utf-8'))
    payload = json.loads(HISTORY.read_text(encoding='utf-8')) if HISTORY.exists() else {'days': {}}
    if not isinstance(payload.get('days'), dict):
        raise ValueError('Invalid local songwriting history; refusing to overwrite it')
    cfg = _r2_config()
    s3 = None
    if cfg:
        from botocore.exceptions import ClientError
        s3 = cfg['boto3'].client('s3', endpoint_url=f"https://{cfg['account']}.r2.cloudflarestorage.com",
            aws_access_key_id=cfg['key_id'], aws_secret_access_key=cfg['secret'])
        try:
            remote = json.loads(s3.get_object(Bucket=cfg['bucket'], Key=R2_KEY)['Body'].read())
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') not in {'NoSuchKey', '404', 'NotFound'}:
                raise
            remote = {'days': {}}
        if not isinstance(remote.get('days'), dict):
            raise ValueError('Invalid remote songwriting history; refusing to overwrite it')
        for day, rows in remote['days'].items():
            local_rows = payload['days'].setdefault(day, {})
            for tid, point in rows.items():
                if tid in local_rows and local_rows[tid] != point:
                    raise ValueError(f'Conflicting local/R2 observation: {day} {tid}')
                local_rows[tid] = point
    days = payload['days']
    current = days.setdefault(stats_date, {})
    with requests.Session() as session:
        for track in tracks:
            tid = track['track_id']
            existing = current.get(tid)
            # A dated observation cannot be replaced by later live counters.
            if existing is not None:
                if existing.get('reason'):
                    logger.warning('[collections] %s %s pending: %s', stats_date, tid, existing['reason'])
                continue
            total = fetch_playcount_api(tid, token_mgr, session)
            point = exact_point(total, days.get(previous_date, {}).get(tid))
            if point.get('reason'):
                logger.warning('[collections] %s %s pending: %s', stats_date, tid, point['reason'])
            if point['s'] is not None:
                current[tid] = point
            else:
                # Log the exact failure without storing an unusable observation.
                pending = ROOT / 'runtime/collections' / f'{stats_date}-pending.json'
                pending.parent.mkdir(parents=True, exist_ok=True)
                pending.write_text(json.dumps({'date': stats_date, 'track_id': tid, **point}, indent=2)+'\n', encoding='utf-8')
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)+'\n'
    temporary = HISTORY.with_suffix('.json.tmp')
    temporary.write_text(encoded, encoding='utf-8')
    temporary.replace(HISTORY)
    if s3 is not None:
        s3.put_object(Bucket=cfg['bucket'], Key=R2_KEY, Body=encoded.encode('utf-8'),
            ContentType='application/json', CacheControl='public, max-age=60')
    elif os.getenv('UPLOAD_TO_R2', '').lower() not in {'0', 'false', 'no', 'off'}:
        logger.warning('[collections] R2 upload unavailable; songwriting remains pending on the site')
