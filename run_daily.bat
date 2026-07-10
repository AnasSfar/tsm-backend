@echo off
title TSM Daily Tasks
cd /d "C:\Users\sfara\Documents\GitHub\tsm-backend"

set TSM_HEADLESS=1
set TWITTER_HEADLESS=1

echo ========================================
echo  TSM Daily - %date% %time%
echo ========================================
echo.

echo [1/1] TSM daily collectors...
"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" -m tsm daily %*
if errorlevel 1 goto :error
echo.

echo.
echo ========================================
echo  Termine - %date% %time%
echo ========================================
pause
exit /b 0

:error
echo.
echo ========================================
echo  Erreur: une etape a echoue
echo ========================================
pause
exit /b 1
