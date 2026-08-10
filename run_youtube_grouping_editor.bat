@echo off
title TSM YouTube Grouping Editor
cd /d "%~dp0"

echo ========================================
echo  Combiner les videos YouTube - TSM
echo ========================================
echo.

"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" scripts\youtube_grouping_editor\server.py %*
