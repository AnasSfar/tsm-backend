"""Build the OG | Taylor's Version split covers used by the frontend for combined eras.

Same composition as the Top Eras image (generate_albums_image.split_cover_data_uri):
left half = original album cover, right half = Taylor's Version cover, cut vertically
in the middle. Covers come from db/discography/covers.json (Spotify artwork).

Writes <frontend>/frontend/public/covers/split/<era>.webp (fearless, speak now, red, 1989),
served by the site itself (no CSP / CORS concern). Covers never change, so this only needs
re-running if covers.json changes for one of these albums.

  python scripts/build_split_era_covers.py [--frontend PATH] [--size 640]
"""
from __future__ import annotations

import argparse
import base64
import io
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "collectors/spotify/streams/tools/scripts"))

import generate_albums_image as ga  # noqa: E402
from comp.tables_image import download_as_data_uri  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frontend", default=str(REPO.parent / "tsm-frontend"),
                        help="tsm-frontend repo root (default: sibling folder)")
    parser.add_argument("--size", type=int, default=640, help="square output size in px")
    args = parser.parse_args()

    out_dir = Path(args.frontend) / "frontend/public/covers/split"
    out_dir.mkdir(parents=True, exist_ok=True)
    covers = ga.load_covers()
    for era, (og_album, tv_album) in ga.ERA_SPLIT_COVER.items():
        og_url = covers.get(ga._norm(og_album), "")
        tv_url = covers.get(ga._norm(tv_album), "")
        og_uri, tv_uri = download_as_data_uri(og_url), download_as_data_uri(tv_url)
        if not (og_uri and tv_uri):
            print(f"[split_covers] {era}: cover missing (og={bool(og_uri)}, tv={bool(tv_uri)}), skipped")
            continue
        combined = ga.split_cover_data_uri(og_uri, tv_uri, size=args.size)
        if not combined:
            continue
        img = Image.open(io.BytesIO(base64.b64decode(combined.split(",", 1)[1])))
        path = out_dir / f"{era.lower()}.webp"
        img.save(path, format="WEBP", quality=88)
        print(f"[split_covers] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
