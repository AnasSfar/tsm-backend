@echo off
title TSM Daily Tasks
cd /d "C:\Users\sfara\Documents\GitHub\tsm-backend"

echo ========================================
echo  TSM Daily - %date% %time%
echo ========================================
echo.

echo [1/3] Spotify Charts FR + Global (parallel)...
powershell -NoProfile -Command "$p1 = Start-Process -FilePath 'C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe' -ArgumentList 'collectors\spotify\charts\fr\daily.py' -WorkingDirectory 'C:\Users\sfara\Documents\GitHub\tsm-backend' -PassThru; $p2 = Start-Process -FilePath 'C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe' -ArgumentList 'collectors\spotify\charts\global\daily.py' -WorkingDirectory 'C:\Users\sfara\Documents\GitHub\tsm-backend' -PassThru; Wait-Process -Id $p1.Id,$p2.Id; if ($p1.ExitCode -ne 0 -or $p2.ExitCode -ne 0) { exit 1 }"
if errorlevel 1 goto :error
echo.

echo [2/3] Spotify Streams Update...
"C:\Users\sfara\AppData\Local\Microsoft\WindowsApps\python3.13.exe" collectors\spotify\streams\update_streams.py
if errorlevel 1 goto :error
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
