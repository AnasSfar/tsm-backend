import json
import sys
from pathlib import Path

HISTORY_PATH = Path("data/history.json")


def delete_day(target_date: str) -> None:
    if not HISTORY_PATH.exists():
        print(f"Fichier introuvable: {HISTORY_PATH}")
        return

    data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))

    dates = data.get("dates", [])
    by_date = data.get("by_date", {})
    summary = data.get("summary", {})

    existed = False

    if target_date in dates:
        dates.remove(target_date)
        existed = True

    if target_date in by_date:
        del by_date[target_date]
        existed = True

    if not existed:
        print(f"Aucune donnée trouvée pour {target_date}")
        return

    dates = sorted(dates)
    data["dates"] = dates
    data["by_date"] = by_date

    if "summary" not in data or not isinstance(data["summary"], dict):
        data["summary"] = {}

    data["summary"]["latest_date"] = dates[-1] if dates else None

    HISTORY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Date supprimée : {target_date}")
    print(f"Nouvelle latest_date : {data['summary']['latest_date']}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/delete_history_day.py 2026-03-08")
        return

    target_date = sys.argv[1].strip()
    delete_day(target_date)


if __name__ == "__main__":
    main()