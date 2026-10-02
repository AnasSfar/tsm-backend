"""Local preview API for the TS Top Songs "Charts" column (2026-10-02).

Runs the REAL tsm-frontend FastAPI app on :8013 with TSM_DATA_SOURCE=local
(real local exports, read-only), untouched. The chart_rank graft happens in
the browser (shoot.py, Playwright page.route) because /api/apple-music's live
path no longer goes through load_apple_music.

  python previews_and_sims/ts-top-songs-charts-column/preview_api.py
  npx vite --config previews_and_sims/ts-top-songs-charts-column/vite.preview.config.mjs
  open http://localhost:3010/amcharts/apple-music?section=taylor_swift&tab=ts_top_songs
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
from api.index import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8013, log_level="warning")
