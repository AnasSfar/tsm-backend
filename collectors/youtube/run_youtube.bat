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
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    >> "%LOG%" echo  Erreur collecte YouTube - %date% %time%
) else (
    >> "%LOG%" echo  Termine - %date% %time%
)

REM Extinction du PC apres le run (2026-10-13) : TSM_SHUTDOWN_AFTER=1 n'est pose
REM QUE par l'action de la tache planifiee 06:05 ; garde horaire avant 9h dans le
REM script. Fenetre d'avertissement 3 min avec bouton "Annuler l'extinction".
if "%TSM_SHUTDOWN_AFTER%"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "collectors\youtube\shutdown_after_run.ps1"

if not "%RC%"=="0" exit /b 1
exit /b 0
