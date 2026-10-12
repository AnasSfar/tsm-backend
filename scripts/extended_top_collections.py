"""Explicit editorial membership. Never reads or writes stream histories."""
import json
from pathlib import Path

MANIFEST = Path(__file__).with_name('top_collection_memberships.json')


def extend(track):
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    albums = [[track(a['catalog'], title) for title in a['standard']] for a in manifest['albums']]
    result = []

    def add(slug, title, kind, tracks, image='/headers/all-eras.jpg'):
        ids = [t['track_id'] for t in tracks]
        if not tracks or len(ids) != len(set(ids)):
            raise ValueError(f'Empty or duplicate membership: {slug}')
        result.append(dict(slug=slug, title=title, kind=kind, image=image, tracks=tracks))

    for index in range(max(map(len, albums))):
        add(f'track-{index+1}', f'Track {index+1}', 'track-numbers', [a[index] for a in albums if len(a) > index])
    add('album-openers', 'Album Openers', 'categories', [a[0] for a in albums])
    add('album-closers', 'Album Closers', 'categories', [a[-1] for a in albums])
    for c in manifest['collections']:
        add(c['slug'], c['title'], c['kind'], [track(album, title) for album, title in c['members']], c.get('image', '/headers/all-eras.jpg'))
        if c.get('membership_issues'):
            result[-1]['membership_issues'] = c['membership_issues']
    return result
