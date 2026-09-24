@echo off
cd /d "C:\Users\sfara\Documents\GitHub\tsm-backend"
set "PY=C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe"

REM Apple Music (streaming / most-played charts)
REM -u = stdout non bufferise, sinon le log parait fige pendant tout le run
REM (meme piege que Spotify Streams, cf. skill pipeline-ops).
"%PY%" -u collectors\apple_music\run_apple_music.py >> collectors\apple_music\run_apple_music.log 2>&1

REM iTunes Store (purchase charts) — same schedule, launched right after so the
REM MusicKit token cache is already warm. Runs regardless of the Apple Music
REM exit code; own log. No separate Task Scheduler entry needed.
"%PY%" -u collectors\itunes\run_itunes.py >> collectors\itunes\run_itunes.log 2>&1

REM New-release hourly chart-movement posts (added 2026-09-24, The Encore).
REM No-op outside a track's debut window (default 72h) — safe to always run.
REM Reads this cycle's CSVs from BOTH steps above, so it must stay last.
"%PY%" -u collectors\apple_music\post_new_release_progression.py >> collectors\apple_music\post_new_release_progression.log 2>&1
