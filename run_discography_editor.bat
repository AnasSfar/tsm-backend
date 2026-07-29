@echo off
title TSM Discography Editor
cd /d "%~dp0"

echo ========================================
echo  Editeur de discographie - TSM
echo ========================================
echo.

"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" scripts\discography_editor\server.py %*
