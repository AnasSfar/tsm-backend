@echo off
cd /d "C:\Users\sfara\Documents\GitHub\tsm-backend"
set "PY=C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe"
set "NTFY=https://ntfy.sh/taylormuseum-apple-music"

REM iTunes Store chain (purchase charts) — launched IN PARALLEL by
REM collectors\apple_music\run_apple_music.bat (2026-09-24, owner: "whoever
REM is ready first is posted first"): collect (~2 min) then post the iTunes
REM new-release progression right away, without waiting for Apple Music.
REM Also usable alone for a manual run / catch-up (run_itunes_hidden.vbs).
"%PY%" -u collectors\itunes\run_itunes.py >> collectors\itunes\run_itunes.log 2>&1
set "RC=%ERRORLEVEL%"

REM 75 = previous iTunes run still alive (single-instance lock in run_itunes.py,
REM 2026-09-25): skip this hour entirely, posts included - the other run posts.
if "%RC%"=="75" exit /b 0
REM 3 = failed and already alerted by Python. Any other non-zero code = Python
REM died before it could alert (import error, broken interpreter): alert here.
if not "%RC%"=="0" if not "%RC%"=="3" "%SystemRoot%\System32\curl.exe" -s -m 20 -H "Title: iTunes collector" -H "Priority: high" -H "Tags: warning" -d "run_itunes.py crashed before it could alert (exit %RC%). Check collectors\itunes\run_itunes.log" %NTFY% >nul 2>&1

REM No-op outside a track's debut window (default 72h). Own log: the Apple
REM Music chain appends to its own post log at the same time.
"%PY%" -u collectors\apple_music\post_new_release_progression.py --platform itunes >> collectors\apple_music\post_new_release_progression_itunes.log 2>&1
set "RC=%ERRORLEVEL%"
REM The post script catches and alerts everything itself (exit 0): non-zero =
REM it could not even start.
if not "%RC%"=="0" "%SystemRoot%\System32\curl.exe" -s -m 20 -H "Title: iTunes new-release posts" -H "Priority: high" -H "Tags: warning" -d "post_new_release_progression.py (iTunes) crashed at startup (exit %RC%). Check collectors\apple_music\post_new_release_progression_itunes.log" %NTFY% >nul 2>&1
