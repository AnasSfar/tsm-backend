import json, subprocess

# Récupérer la version précédente du fichier
old_content = subprocess.check_output(['git', 'show', '4eb486c2:db/discography/albums/the_life_of_a_showgirl.json'], text=True)
old_data = json.loads(old_content)

# Chercher Elizabeth Taylor
for section in old_data.get('sections', []):
    for track in section.get('tracks', []):
        if 'Elizabeth Taylor' in track.get('title', ''):
            print(f"{track.get('title')}: {track.get('url')}")
