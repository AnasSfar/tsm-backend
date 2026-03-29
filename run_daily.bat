@echo off
title TSM Daily Tasks
cd /d "C:\Users\sfara\Documents\GitHub\tsm-backend"

echo ========================================
echo  TSM Daily - %date% %time%
echo ========================================
echo.

echo [1/3] Spotify Charts FR...
"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" collectors\spotify\charts\fr\daily.py
echo.

echo [2/3] Spotify Charts Global...
"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" collectors\spotify\charts\global\daily.py
echo.

echo [3/3] Spotify Streams Update...
"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" collectors\spotify\streams\update_streams.py
echo.

echo ========================================
echo  Termine - %date% %time%
echo ========================================
pause
