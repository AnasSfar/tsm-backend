"""Build explicit collection membership from exact catalog titles (no runtime matching).

Usage: python scripts/build_top_collections.py --output /path/to/collections.json
Review the emitted Spotify IDs whenever changing membership. No stream data is written.
"""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'db/discography'


def track(album, title):
    path = CATALOG / ('misc.json' if album == 'misc' else f'albums/{album}.json')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    sections = data['sections'] if isinstance(data, dict) else data
    matches = [t for s in sections for t in s['tracks'] if t['title'] == title]
    ids = {re.search(r'/track/([A-Za-z0-9]{22})', t['url']).group(1) for t in matches}
    if len(ids) != 1:
        raise ValueError(f'Ambiguous or missing catalog entry: {album}: {title}: {ids}')
    t = matches[0]
    return {'track_id': ids.pop(), 'title': title, 'artist': t['primary_artist'],
            'source': 'catalog', 'catalog_file': str(path.relative_to(ROOT)).replace('\\', '/'),
            'image_url': t.get('image_url'), 'release_date': t.get('release_date')}


def group(album, names, tv=False):
    return [track(album, name + (" (Taylor's Version)" if tv else '')) for name in names.split('|')]


def build():
    lover = group('lover', 'Miss Americana & The Heartbreak Prince|Cruel Summer|The Man|You Need To Calm Down|Lover|The Archer')
    fearless = group('fearless_taylor_s_version', 'Fearless|You Belong With Me|Love Story', True)
    evermore = group('evermore', "'tis the damn season|willow|marjorie|champagne problems|tolerate it")
    reputation = group('reputation', "...Ready For It?|Delicate|Don't Blame Me|Look What You Made Me Do")
    speak = group('speak_now_taylor_s_version', 'Enchanted', True)
    red = group('red_taylor_s_version', '22|We Are Never Ever Getting Back Together|I Knew You Were Trouble', True) + [track('red_taylor_s_version', "All Too Well (10 Minute Version) (Taylor's Version) (From The Vault)")]
    folklore = group('folklore', 'invisible string|betty|the last great american dynasty|august|illicit affairs|my tears ricochet|cardigan')
    pop = group('1989_taylor_s_version', 'Style|Blank Space|Shake It Off|Wildest Dreams|Bad Blood', True)
    midnight = group('midnights', 'Lavender Haze|Anti-Hero|Midnight Rain|Vigilante Shit|Bejeweled|Mastermind|Karma')
    poets = group('the_tortured_poets_department', "But Daddy I Love Him|So High School|Who's Afraid of Little Old Me?|Down Bad|Fortnight (feat. Post Malone)|The Smallest Man Who Ever Lived|I Can Do It With a Broken Heart")
    rep = group('reputation', '...Ready For It?|I Did Something Bad|Gorgeous')
    rep += group('1989_taylor_s_version', 'Style', True) + group('fearless_taylor_s_version', 'Love Story|You Belong With Me', True)
    rep += group('reputation', "Look What You Made Me Do|End Game|King Of My Heart|Delicate")
    rep += group('1989_taylor_s_version', 'Shake It Off', True) + group('reputation', 'Dancing With Our Hands Tied|Dress')
    rep += group('1989_taylor_s_version', 'Bad Blood', True) + group('taylor_swift', "Should've Said No")
    rep += group('reputation', "Don't Blame Me|Getaway Car|Call It What You Want|New Year's Day")
    rep += group('speak_now_taylor_s_version', 'Long Live', True) + group('red_taylor_s_version', 'We Are Never Ever Getting Back Together', True) + group('reputation', "This Is Why We Can't Have Nice Things")
    soundtracks = group('red_taylor_s_version', 'Eyes Open', True) + [track('red_taylor_s_version', "Safe & Sound (feat. Joy Williams and John Paul White) (Taylor's Version)")]
    soundtracks += [track('1989', 'Sweeter Than Fiction - From "One Chance" Soundtrack')]
    soundtracks += [track('folklore', 'Carolina - From The Motion Picture “Where The Crawdads Sing”'), track('lover', 'Only The Young - Featured in Miss Americana'), track('reputation', "I Don't Wanna Live Forever (Fifty Shades Darker)")]
    soundtracks += group('misc', 'Macavity - From The Motion Picture Soundtrack "Cats"|Beautiful Ghosts - From The Motion Picture Soundtrack "Cats"|I Knew It, I Knew You')
    collabs = group('misc', 'Gasoline (feat. Taylor Swift)|Renegade|Birch|The Alcott (feat. Taylor Swift)|Two Is Better Than One (feat. Taylor Swift)|us. (feat. Taylor Swift)|The Joker And The Queen (feat. Taylor Swift)|Both of Us (feat. Taylor Swift)|Highway Don’t Care (feat. Taylor Swift & Keith Urban)')
    singles = group('taylor_swift', 'Tim McGraw|Teardrops On My Guitar - Radio Single Remix|Our Song|Picture To Burn|Should\'ve Said No')
    singles += group('fearless_taylor_s_version', 'Love Story|White Horse|You Belong With Me|Fifteen|Fearless', True)
    singles += group('speak_now_taylor_s_version', 'Mine|Back To December|Mean|The Story Of Us|Sparks Fly|Ours', True)
    singles += group('red_taylor_s_version', 'We Are Never Ever Getting Back Together|Begin Again|I Knew You Were Trouble|22|Red', True)
    singles += group('1989_taylor_s_version', 'Shake It Off|Blank Space|Style|Bad Blood|Wildest Dreams|Out Of The Woods|New Romantics', True)
    singles += group('reputation', 'Look What You Made Me Do|...Ready For It?|End Game|Delicate') + group('lover', 'ME! (feat. Brendon Urie of Panic! At The Disco)|You Need To Calm Down|Lover|The Man|Cruel Summer')
    singles += group('folklore', 'cardigan|exile (feat. Bon Iver)|betty') + group('evermore', 'willow|no body, no crime (feat. HAIM)|coney island (feat. The National)') + group('midnights', 'Anti-Hero|Lavender Haze|Karma') + group('the_tortured_poets_department', 'Fortnight (feat. Post Malone)|I Can Do It With a Broken Heart')
    external_track = json.loads((ROOT / 'db/collections/songwriting_tracks.json').read_text(encoding='utf-8'))[0]
    external = {'track_id': external_track['track_id'], 'title': external_track['title'],
                'artist': ', '.join(external_track['artists']), 'source': 'songwriting',
                'release_date': '2016-04-29', 'image_url': None}
    songwriting = [external, track('misc', 'Better Man')]
    defs = [
        ('eras-tour-2023', 'The Eras Tour · 2023', 'setlists', '/headers/all-eras.jpg', 'Opening-night main set (Glendale, March 17). Surprise songs and spoken interludes excluded.', lover+fearless+evermore+reputation+speak+red+folklore+pop+midnight),
        ('eras-tour-2024', 'The Eras Tour · 2024', 'setlists', '/headers/the tortured poets department.png', 'Paris main set (May 9), including TTPD. Surprise songs excluded; mashup songs counted separately.', lover[:5]+fearless+red+speak+reputation+group('folklore','cardigan|betty')+group('evermore','champagne problems')+group('folklore','august|illicit affairs|my tears ricochet')+group('evermore','marjorie|willow')+pop+poets+midnight),
        ('reputation-tour', 'reputation Stadium Tour', 'setlists', '/headers/reputation.png', 'Regular main set. Surprise songs and guest additions excluded; mashup songs counted separately.', rep),
        ('singles', 'Singles', 'categories', '/headers/all-eras.jpg', 'Our curated selection of album singles through TTPD.', singles),
        ('soundtracks', 'Soundtracks', 'categories', '/headers/folklore.png', 'Our curated selection of studio soundtrack recordings performed by Taylor.', soundtracks),
        ('collabs', 'Collabs', 'categories', '/headers/red.png', 'Our curated selection of studio collaborations on other artists’ releases.', collabs),
        ('songwriting', 'Songwriting', 'categories', '/headers/red (taylor\'s version).png', 'Written by Taylor, performed by other artists. Initial selection: Calvin Harris / Rihanna and Little Big Town.', songwriting),
    ]
    result = []
    for slug, title, kind, image, description, tracks in defs:
        ids = [t['track_id'] for t in tracks]
        if len(ids) != len(set(ids)): raise ValueError(f'Duplicate membership: {slug}')
        result.append(dict(slug=slug,title=title,kind=kind,image=image,description=description,tracks=tracks))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    data = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print([(c['slug'], len(c['tracks'])) for c in data])
