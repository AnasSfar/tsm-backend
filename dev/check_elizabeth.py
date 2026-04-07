import json

songs = json.load(open('db/discography/songs.json'))
et = [s for s in songs if 'Elizabeth Taylor' in s.get('title', '')]
print(f'Found {len(et)} entries')
for i, s in enumerate(et):
    print(f"{i}: {s.get('title')} => {s.get('url')}")
