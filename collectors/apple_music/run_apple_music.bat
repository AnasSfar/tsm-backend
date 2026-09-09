@echo off
cd /d "C:\Users\sfara\Documents\GitHub\tsm-backend"
set "PY=C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe"

REM Apple Music (streaming / most-played charts)
"%PY%" collectors\apple_music\run_apple_music.py >> collectors\apple_music\run_apple_music.log 2>&1

REM iTunes Store (purchase charts) — same schedule, launched right after so the
REM MusicKit token cache is already warm. Runs regardless of the Apple Music
REM exit code; own log. No separate Task Scheduler entry needed.
"%PY%" collectors\itunes\run_itunes.py >> collectors\itunes\run_itunes.log 2>&1
