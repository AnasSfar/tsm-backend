@echo off
cd /d "%~dp0"
"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" -m tsm collect charts %*
