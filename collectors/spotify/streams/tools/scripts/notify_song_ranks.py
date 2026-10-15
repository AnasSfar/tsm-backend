"""Compatibility command: Taylor positions in extra_taylor's Top2026 recap only."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO=Path(__file__).resolve().parents[5]


def notify_for_date(target, *, dry_run=False, retry_send=False):
    root=Path(os.getenv('EXTRA_TAYLOR_ROOT') or REPO.parent/'extra_taylor')
    script=root/'notify_top2026_taylor.py'
    if not script.is_file():raise FileNotFoundError(f'Top2026 notifier missing: {script}')
    cmd=[sys.executable,str(script),target]
    if dry_run:cmd.append('--dry-run')
    if retry_send:cmd.append('--retry-send')
    subprocess.run(cmd,cwd=root,check=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('date')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--retry-send',action='store_true')
    args=p.parse_args()
    notify_for_date(args.date,dry_run=args.dry_run,retry_send=args.retry_send)


if __name__=='__main__':main()
