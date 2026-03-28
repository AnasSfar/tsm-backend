import json
from pathlib import Path

ROOT = Path("site")
DATA = ROOT / "data"
OUT = ROOT / "history"

OUT.mkdir(exist_ok=True)

history = json.loads((DATA / "history.json").read_text())

for date, data in history["by_date"].items():
    (OUT / f"{date}.json").write_text(json.dumps(data))

print("History split done")