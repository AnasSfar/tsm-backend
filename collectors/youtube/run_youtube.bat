@echo off
title TSM YouTube Views
cd /d "C:\Users\sfara\Documents\GitHub\tsm-backend"

REM Log en append (gitignored, *.log) : avant le 2026-10-03 la sortie partait
REM dans une console cachee, d'ou aucun traceback pour le crash du 2026-10-02.
REM -u = stdout non bufferise (sinon le log parait fige pendant le run).
set "LOG=collectors\youtube\run_youtube.log"

>> "%LOG%" echo.
>> "%LOG%" echo ========================================
>> "%LOG%" echo  TSM YouTube Views - %date% %time%
>> "%LOG%" echo ========================================

C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe -u -m collectors.youtube.videos.update_youtube --commit --no-notify >> "%LOG%" 2>&1
if errorlevel 1 goto :error

>> "%LOG%" echo  Termine - %date% %time%
exit /b 0

:error
>> "%LOG%" echo  Erreur collecte YouTube - %date% %time%
exit /b 1
