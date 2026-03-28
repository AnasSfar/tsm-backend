from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class SongEntry:
    song_name: str
    rank: int
    image_url: str = ""
    apple_music_id: str = ""
    url: str = ""
    artist_name: str = ""
    album_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
