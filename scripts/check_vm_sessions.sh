#!/usr/bin/env bash
set -u

cd /home/anassfar_fr/tsm-backend

for p in \
  collectors/spotify/charts/global/tools/json \
  collectors/spotify/charts/fr/tools/json \
  collectors/spotify/charts/worldwide/tools/json \
  collectors/spotify/charts/us/tools/json \
  collectors/spotify/charts/uk/tools/json
do
  echo "== $p =="
  if [ ! -d "$p" ]; then
    echo "missing_dir"
    continue
  fi
  for f in spotify_session.json twitter_session.json bearer_cache.json cookies_twitter.json chrome_profile; do
    if [ -e "$p/$f" ]; then
      if [ -d "$p/$f" ]; then
        count=$(find "$p/$f" -type f 2>/dev/null | wc -l)
        size=$(du -sh "$p/$f" 2>/dev/null | cut -f1)
        echo "$f DIR files=$count size=$size"
      else
        size=$(wc -c < "$p/$f" 2>/dev/null)
        echo "$f FILE bytes=$size"
      fi
    else
      echo "$f MISSING"
    fi
  done
done
