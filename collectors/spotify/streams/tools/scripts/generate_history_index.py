import json
from pathlib import Path

HISTORY_DIR = Path("site/history")
INDEX_PATH = HISTORY_DIR / "index.json"

dates = sorted(
    p.stem
    for p in HISTORY_DIR.glob("*.json")
    if p.stem != "index"
)

with open(INDEX_PATH, "w", encoding="utf-8") as f:
    json.dump({"dates": dates}, f, indent=2)

print(f"{len(dates)} dates écrites dans history/index.json")