"""Generate the combined TayBoard songs chart."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate combined TayBoard songs chart")
    p.add_argument("--date", dest="date", default=None, help="Week ending date (YYYY-MM-DD)")
    p.add_argument("--backfill", dest="backfill", action="store_true",
                   help="Generate all available weekly snapshots from streams history")
    p.add_argument("--force", dest="force", action="store_true",
                   help="With --backfill: regenerate weeks that already have a snapshot")
    p.add_argument("--streams-csv", dest="streams_csv", default=None,
                   help="Path to streams CSV to use instead of streams_history.csv")
    p.add_argument("--rebuild-index", dest="rebuild_index", action="store_true",
                   help="Rebuild swift_top_100_index.json from existing snapshots and upload to R2")
    p.add_argument("--generate-songs", dest="generate_songs", action="store_true",
                   help="Regenerate per-song history JSON files from existing snapshots and upload to R2")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="Compute only; do not write files")
    p.add_argument("--skip-r2", dest="skip_r2", action="store_true", help="Do not upload generated files to R2")
    p.add_argument("--skip-images", dest="skip_images", action="store_true",
                   help="Do not generate PNG chart images")
    return p.parse_args(argv)


def run_from_args(args: argparse.Namespace, *, engine: ModuleType | None = None) -> int:
    if engine is None:
        import swift_top_100 as engine

    next_args = argparse.Namespace(**vars(args))
    next_args.variant = "combined"
    engine._configure_variant("combined")
    try:
        engine.main_from_args(next_args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run_from_args(parse_args(argv)))


if __name__ == "__main__":
    main()
