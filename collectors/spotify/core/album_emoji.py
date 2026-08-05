from __future__ import annotations

import sys
from pathlib import Path

_COLLECTORS_ROOT = Path(__file__).resolve().parents[2]
if str(_COLLECTORS_ROOT) not in sys.path:
    sys.path.insert(0, str(_COLLECTORS_ROOT))

from twitter.albums import ALBUM_HEARTS as ALBUM_EMOJI, album_emoji

__all__ = ["ALBUM_EMOJI", "album_emoji"]
