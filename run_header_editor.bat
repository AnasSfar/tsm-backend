@echo off
title TSM Header Editor
cd /d "%~dp0"

echo ========================================
echo  Editeur de headers - TSM
echo ========================================
echo.

"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" scripts\header_editor\server.py %*
