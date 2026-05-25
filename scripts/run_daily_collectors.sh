#!/usr/bin/env bash
set -u

cd /home/anassfar_fr/tsm-backend

export TWITTER_HEADLESS=1
export PYTHONUNBUFFERED=1

PY=/home/anassfar_fr/tsm-backend/.venv/bin/python
FAILED=0

echo "===== TSM daily collectors start $(date -Is) ====="

ensure_git_identity() {
  if ! git config user.name >/dev/null; then
    git config user.name "${GIT_AUTHOR_NAME:-TSM Runner}"
  fi
  if ! git config user.email >/dev/null; then
    git config user.email "${GIT_AUTHOR_EMAIL:-runner@thetsmuseum.app}"
  fi
}

run_step() {
  name="$1"
  shift
  echo "----- ${name} start $(date -Is) -----"
  "$@"
  rc=$?
  echo "----- ${name} end rc=${rc} $(date -Is) -----"
  if [ "$rc" -ne 0 ]; then
    FAILED=1
  fi
  return "$rc"
}

ensure_git_identity

run_step "spotify_streams" "$PY" collectors/spotify/streams/update_streams.py || true
run_step "spotify_charts" "$PY" collectors/spotify/charts/run_all_charts.py || true
run_step "youtube" "$PY" -m collectors.youtube.update_youtube || true
run_step "billboard" "$PY" collectors/billboard/scrape_billboard.py || true

echo "===== TSM daily collectors end $(date -Is) ====="

if [ "$FAILED" -ne 0 ]; then
  echo "===== TSM daily collectors FAILED $(date -Is) ====="
  exit 1
fi

echo "===== TSM daily collectors OK $(date -Is) ====="
