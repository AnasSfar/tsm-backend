"""Local preview API for the live TS Top Songs toggle.

Runs the REAL tsm-frontend FastAPI app on :8003 (the Vite dev proxy target)
with TSM_DATA_SOURCE=local (reads the backend's real local exports, read-only)
and ONLY injects `ts_top_songs_live` from this sim's out/ts_top_songs_live.json
(built by simulate.py from today's real raw cycles). Nothing is written.

  python previews_and_sims/ts-top-songs-live/preview_api.py
  (then `npm run dev` in tsm-frontend/frontend and open /amcharts/apple-music?section=taylor_swift&tab=ts_top_songs)
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRONTEND = Path(r"C:\Users\sfara\Documents\GitHub\tsm-frontend")
os.environ["TSM_DATA_SOURCE"] = "local"
sys.path.insert(0, str(FRONTEND))

import uvicorn  # noqa: E402
from api.data import loader  # noqa: E402
from api.routes import apple_music  # noqa: E402

live = json.loads((HERE / "out" / "ts_top_songs_live.json").read_text(encoding="utf-8"))
_real = apple_music.load_apple_music


def _with_live():
    data = dict(_real())
    data["ts_top_songs_live"] = live
    return data


apple_music.load_apple_music = _with_live

from api.index import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8003, log_level="warning")
