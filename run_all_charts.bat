@echo off
cd /d "%~dp0"
set TSM_HEADLESS=1
set TWITTER_HEADLESS=1
"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" -m tsm collect charts %*
