@echo off
title TSM YouTube Views
cd /d "C:\Users\sfara\Documents\GitHub\tsm-backend"

echo ========================================
echo  TSM YouTube Views - %date% %time%
echo ========================================
echo.

C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe -m collectors.youtube.update_youtube --commit
if errorlevel 1 goto :error

echo.
echo ========================================
echo  Termine - %date% %time%
echo ========================================
exit /b 0

:error
echo.
echo ========================================
echo  Erreur collecte YouTube
echo ========================================
exit /b 1
