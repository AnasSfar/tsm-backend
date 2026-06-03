from __future__ import annotations

from pathlib import Path


def should_skip_post(
    lock_path: Path,
    *,
    target_date: str,
    label: str,
    no_post: bool,
    regenerate_when_no_post: bool = True,
) -> bool:
    """Return True when a per-post lock should skip a real post."""
    if lock_path.exists() and not no_post:
        print(f"{label} already posted for {target_date}, skipping.")
        return True
    if lock_path.exists() and no_post and regenerate_when_no_post:
        print(f"{label} already posted for {target_date}, regenerating only (--no-post).")
    return False


def mark_posted(lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch()
