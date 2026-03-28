#!/usr/bin/env python3
"""
git_ops.py — git commit/push helpers for the streams daily pipeline.
"""
import subprocess
from datetime import date
from pathlib import Path


def git_commit_and_push(repo_root: Path, message: str | None = None) -> None:
    try:
        subprocess.run(
            [
                "git", "add",
                "collectors/spotify/streams/history/",
                "db/",
                "website/site/data/",
                "website/site/history/",
            ],
            cwd=str(repo_root),
            check=True,
        )
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(repo_root),
            check=False,
        )
        if diff.returncode == 0:
            print("No git changes to commit.")
            return

        msg = message or f"streams {date.today().isoformat()}"
        subprocess.run(["git", "commit", "-m", msg], cwd=str(repo_root), check=True)
        subprocess.run(["git", "push"], cwd=str(repo_root), check=True)
        print("Git commit + push done.")
    except subprocess.CalledProcessError as e:
        print(f"Git commit/push failed: {e}")
