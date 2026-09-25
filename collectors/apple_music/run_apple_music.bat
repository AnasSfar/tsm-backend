@echo off
cd /d "C:\Users\sfara\Documents\GitHub\tsm-backend"
set "PY=C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe"

REM iTunes Store chain (purchase charts + its own new-release posts) starts
REM FIRST and IN PARALLEL (2026-09-24, owner: "they need to be updated
REM independently, whoever is ready first is posted first"). It no longer waits
REM ~15-20 min for Apple Music: iTunes posts land ~HH:03 instead of ~HH:20.
REM The MusicKit token cache it reads is written atomically (core/token.py)
REM and storefront discovery falls back to a fixed list, so racing Apple Music
REM is safe. /b = same hidden console, no window. Absolute path + /d: with a
REM relative path the child cmd did NOT find the .bat in a test (2026-09-24).
REM Verified: the iTunes chain keeps running if this .bat exits first.
start "" /b cmd /d /c "C:\Users\sfara\Documents\GitHub\tsm-backend\collectors\itunes\run_itunes.bat"

REM Apple Music (streaming / most-played charts)
REM -u = stdout non bufferise, sinon le log parait fige pendant tout le run
REM (meme piege que Spotify Streams, cf. skill pipeline-ops).
REM The Apple Music new-release posts (+ Global cards) are started BY
REM run_apple_music.py right after its collectors (2026-09-25), in parallel with
REM export/upload/images — no longer a separate step here, ~15 min earlier.
REM Exit 75 = previous Apple Music run still alive (single-instance lock).
"%PY%" -u collectors\apple_music\run_apple_music.py >> collectors\apple_music\run_apple_music.log 2>&1
set "RC=%ERRORLEVEL%"
REM 0 = ok, 3 = failed and already alerted by Python, 75 = hour skipped (alerted).
REM Anything else = Python died before it could alert: alert from here.
if not "%RC%"=="0" if not "%RC%"=="3" if not "%RC%"=="75" "%SystemRoot%\System32\curl.exe" -s -m 20 -H "Title: Apple Music collector" -H "Priority: high" -H "Tags: warning" -d "run_apple_music.py crashed before it could alert (exit %RC%). Check collectors\apple_music\run_apple_music.log" https://ntfy.sh/taylormuseum-apple-music >nul 2>&1
