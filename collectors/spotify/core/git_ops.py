#!/usr/bin/env python3
"""Small best-effort Git helpers for scheduled collectors."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path


def git_commit_and_push(repo_root: Path, message: str | None = None) -> None:
    """Commit and push all repo changes without failing the collector."""
    repo_root = Path(repo_root)
    message = message or f"scheduled update {date.today().isoformat()}"
    print(f"[{_now()}] [STEP] Git commit et push")
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(repo_root), check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(repo_root), check=False)
        if diff.returncode == 0:
            print(f"[{_now()}] [INFO] Rien a commit.")
            return
        subprocess.run(["git", "commit", "-m", message], cwd=str(repo_root), check=True)
        subprocess.run(["git", "push"], cwd=str(repo_root), check=True)
        print(f"[{_now()}] [INFO] Git commit + push done.")
    except subprocess.CalledProcessError as exc:
        print(f"[{_now()}] [WARN] Git commit/push failed: {exc}")


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
