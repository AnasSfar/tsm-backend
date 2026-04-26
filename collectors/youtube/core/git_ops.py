"""Git commit/push — YouTube views collector."""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def git_commit_and_push(repo_root: Path, message: str) -> None:
    """Stage youtube collector state + CSV, commit and push."""
    try:
        subprocess.run(
            [
                "git", "add",
                "collectors/youtube/tools/json/",
                "db/youtube_views_history.csv",
            ],
            cwd=str(repo_root),
            check=True,
        )
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(repo_root),
            check=False,
        )
        if diff.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(repo_root),
                check=True,
            )
            subprocess.run(["git", "push"], cwd=str(repo_root), check=True)
            print(f"[{_now()}] [INFO] Git commit + push done: {message}")
        else:
            print(f"[{_now()}] [INFO] Rien à commit.")
    except subprocess.CalledProcessError as e:
        print(f"[{_now()}] [WARN] Git commit/push échoué : {e}")
