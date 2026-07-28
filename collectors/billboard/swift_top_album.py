"""Generate the TayBoard albums chart."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate TayBoard albums chart")
    p.add_argument("--date", dest="date", default=None, help="Week ending date (YYYY-MM-DD)")
    p.add_argument("--backfill", dest="backfill", action="store_true",
                   help="Generate all weeks available in swift_top_100_not_combined_songs_history.csv")
    p.add_argument("--force", dest="force", action="store_true",
                   help="With --backfill: regenerate weeks that already have a snapshot")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Compute without writing any files")
    p.add_argument("--rebuild-index", dest="rebuild_index", action="store_true",
                   help="Rebuild swift_top_albums_index.json from existing snapshot files")
    p.add_argument("--skip-r2", dest="skip_r2", action="store_true",
                   help="Do not upload generated files to R2")
    p.add_argument("--skip-images", dest="skip_images", action="store_true",
                   help="Do not generate PNG chart images")
    return p.parse_args(argv)


def run_from_args(args: argparse.Namespace, *, engine: ModuleType | None = None) -> int:
    del engine
    import swift_top_albums as albums

    next_args = argparse.Namespace(**vars(args))
    next_args.variant = "albums"
    albums._configure_variant("albums")
    try:
        albums.main_from_args(next_args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run_from_args(parse_args(argv)))


if __name__ == "__main__":
    main()
