#!/usr/bin/env bash
set -u

cd /home/anassfar_fr/tsm-backend

export TWITTER_HEADLESS=1
export PYTHONUNBUFFERED=1

PY=/home/anassfar_fr/tsm-backend/.venv/bin/python

echo "===== TSM daily collectors start $(date -Is) ====="

run_step() {
  name="$1"
  shift
  echo "----- ${name} start $(date -Is) -----"
  "$@"
  rc=$?
  echo "----- ${name} end rc=${rc} $(date -Is) -----"
  return "$rc"
}

run_step "spotify_streams" "$PY" collectors/spotify/streams/update_streams.py
run_step "spotify_charts" "$PY" collectors/spotify/charts/run_all_charts.py
run_step "youtube" "$PY" collectors/youtube/update_youtube.py
run_step "billboard" "$PY" collectors/billboard/scrape_billboard.py

echo "===== TSM daily collectors end $(date -Is) ====="
