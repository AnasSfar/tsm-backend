#!/usr/bin/env python3
"""
git_ops.py — Git commit/push + CSV migrate operations for Global charts daily.
"""
import subprocess
import sys
from pathlib import Path


def migrate_archive_csv(migrate_script: Path) -> None:
    """Run migrate_charts_to_csv.py to rebuild the global CSV archive."""
    print(f"[{_now()}] [STEP] Mise à jour du CSV charts history")
    result = subprocess.run(
        [sys.executable, str(migrate_script)],
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, flush=True)
    if result.returncode != 0:
        print(f"[{_now()}] [WARN] migrate_charts_to_csv.py a échoué (code {result.returncode})")
    else:
        print(f"[{_now()}] [INFO] CSV charts history mis à jour")


def git_commit_and_push(repo_root: Path) -> None:
    """Stage global/history/ + db/charts_history_global.csv, commit and push."""
    print(f"[{_now()}] [STEP] Git commit et push")
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
            from datetime import date
            today = date.today().isoformat()
            subprocess.run(
                ["git", "commit", "-m", f"charts global {today}"],
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
