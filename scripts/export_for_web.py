#!/usr/bin/env python3
"""
export_for_web.py  (scripts wrapper)
-------------------------------------
Relit db/discography/ + db/songs.db et régénère tous les fichiers
du site : website/site/data/songs.json, albums.json, artist.json,
expected_milestones.json, history/*.json, billboard.json.

À lancer manuellement après avoir modifié db/discography/songs.json
ou des fichiers dans db/discography/albums/ sans attendre le daily update_streams.

Usage:
  python scripts/export_for_web.py
"""
import subprocess
import sys
from pathlib import Path

REAL = Path(__file__).resolve().parents[1] / "collectors" / "spotify" / "streams" / "extras" / "export_for_web.py"

if __name__ == "__main__":
    result = subprocess.run([sys.executable, str(REAL)] + sys.argv[1:])
    sys.exit(result.returncode)
