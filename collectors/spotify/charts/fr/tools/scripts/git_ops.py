#!/usr/bin/env python3
"""
git_ops.py — Git commit/push operations for FR charts daily.
"""
import subprocess
from datetime import date
from pathlib import Path


def git_commit_and_push(repo_root: Path) -> None:
    """Stage fr/history/ + db/charts_history_fr.csv, commit and push."""
    from datetime import date as _date

    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo_root), check=True,
        )
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(repo_root), check=False,
        )
        if diff.returncode != 0:
            today = _date.today().isoformat()
            subprocess.run(
                ["git", "commit", "-m", f"charts FR {today}"],
                cwd=str(repo_root), check=True,
            )
            subprocess.run(["git", "push"], cwd=str(repo_root), check=True)
            print(f"[{_now()}] [INFO] Git commit + push done.")
        else:
            print(f"[{_now()}] [INFO] Rien à commit.")
    except subprocess.CalledProcessError as e:
        print(f"[{_now()}] [WARN] Git commit/push échoué : {e}")


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
