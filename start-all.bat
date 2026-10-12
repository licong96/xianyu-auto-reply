@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "ROOT=%cd%"

echo ==============================================
echo   Xianyu Auto-Reply - Start All Services
echo   Root: %ROOT%
echo ==============================================

rem ---------- 1. Pre-flight checks ----------

rem 1.1 Refuse to run from Recycle Bin
echo %ROOT% | findstr /I /C:"\$RECYCLE.BIN" >nul
if %errorlevel%==0 (
    echo [FAIL] Project is in Recycle Bin. Restore it first.
    exit /b 1
)

rem 1.2 Key files must exist
set MISSING=0
for %%F in (
    backend-web\main.py
    websocket\main.py
    scheduler\main.py
    backend-web\.venv\Scripts\python.exe
    websocket\.venv\Scripts\python.exe
    scheduler\.venv\Scripts\python.exe
    backend-web\app\services\pan\quark_pan_client.py
    frontend\package.json
) do (
    if not exist "%%F" ( echo [FAIL] Missing: %%F & set MISSING=1 )
)
if "%MISSING%"=="1" (
    echo Aborted. Project directory is incomplete.
    exit /b 1
)

rem 1.3 Warn if MySQL/Redis are not reachable (backend exits without them)
for /f %%R in ('powershell -NoProfile -Command "$m = Test-NetConnection -ComputerName 127.0.0.1 -Port 3306 -WarningAction SilentlyContinue -InformationLevel Quiet; $r = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -WarningAction SilentlyContinue -InformationLevel Quiet; if (-not $m) { 'MySQL 3306 unreachable' }; if (-not $r) { 'Redis 6379 unreachable' }" 2^>nul') do (
    echo [WARN] %%R - please start it first (e.g. docker compose up -d)
)

rem ---------- 2. Helpers ----------

rem is_healthy ^<port^>: exit 0 when /health returns HTTP 200 with success
goto :main
:is_healthy
powershell -NoProfile -Command "try { $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:%~1/health' -TimeoutSec 3 -UseBasicParsing; if ($resp.StatusCode -eq 200 -and $resp.Content -match 'success') { exit 0 } else { exit 1 } } catch { exit 1 }"
exit /b %errorlevel%

rem port_pid ^<port^> ^<var^>: set var to PID listening on port (empty if none)
:port_pid
set "%~2="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%~1 .*LISTENING"') do ( set "%~2=%%P" & exit /b 0 )
exit /b 1

rem wait_healthy ^<port^> ^<seconds^>: poll until healthy or timeout
:wait_healthy
set /a TRIES=%~2/2
if %TRIES% LSS 1 set TRIES=1
for /l %%I in (1,1,%TRIES%) do (
    call :is_healthy %~1
    if !errorlevel! equ 0 exit /b 0
    timeout /t 2 /nobreak >nul
)
exit /b 1

rem start_py ^<name^> ^<dir^>: start a python service hidden, detached
:start_py
echo [%~1] Starting...
powershell -NoProfile -Command "Start-Process -FilePath '%ROOT%\%~2\.venv\Scripts\python.exe' -ArgumentList 'main.py' -WorkingDirectory '%ROOT%\%~2' -WindowStyle Hidden -RedirectStandardOutput '%ROOT%\%~2\logs_start.log' -RedirectStandardError '%ROOT%\%~2\logs_start.err.log'"
exit /b 0

rem ---------- 3. Main ----------

:main

rem 3.1 backend-web (8089) - others depend on it
call :is_healthy 8089
if %errorlevel% equ 0 (
    echo [backend-web] Already running on port 8089
) else (
    call :port_pid 8089 PID
    if defined PID (
        echo [backend-web] Port 8089 occupied by PID=%PID% but unhealthy.
        echo             Run stop-all.bat first.
    ) else (
        call :start_py backend-web backend-web
        call :wait_healthy 8089 120
        if !errorlevel! equ 0 ( echo [backend-web] Ready on port 8089 [OK] ) else ( echo [backend-web] Not ready in 120s, see backend-web\logs_start.log )
    )
)

rem 3.2 websocket (8090)
call :is_healthy 8090
if %errorlevel% equ 0 (
    echo [websocket] Already running on port 8090
) else (
    call :port_pid 8090 PID
    if defined PID (
        echo [websocket] Port 8090 occupied by PID=%PID% but unhealthy. Run stop-all.bat first.
    ) else (
        call :start_py websocket websocket
        call :wait_healthy 8090 90
        if !errorlevel! equ 0 ( echo [websocket] Ready on port 8090 [OK] ) else ( echo [websocket] Not ready in 90s, see websocket\logs_start.log )
    )
)

rem 3.3 scheduler (8091)
call :is_healthy 8091
if %errorlevel% equ 0 (
    echo [scheduler] Already running on port 8091
) else (
    call :port_pid 8091 PID
    if defined PID (
        echo [scheduler] Port 8091 occupied by PID=%PID% but unhealthy. Run stop-all.bat first.
    ) else (
        call :start_py scheduler scheduler
        call :wait_healthy 8091 90
        if !errorlevel! equ 0 ( echo [scheduler] Ready on port 8091 [OK] ) else ( echo [scheduler] Not ready in 90s, see scheduler\logs_start.log )
    )
)

rem 3.4 frontend (9000, vite dev server)
call :port_pid 9000 PID
if defined PID (
    echo [frontend] Already running on port 9000
) else (
    echo [frontend] Starting...
    powershell -NoProfile -Command "Start-Process -FilePath cmd.exe -ArgumentList '/c','npm run dev' -WorkingDirectory '%ROOT%\frontend' -WindowStyle Hidden -RedirectStandardOutput '%ROOT%\logs-frontend.txt' -RedirectStandardError '%ROOT%\logs-frontend.err.txt'"
)

rem 3.5 Wait for frontend
set /a N=0
:wait_fe
call :port_pid 9000 PID
if defined PID ( echo [frontend] Ready on port 9000 [OK] & goto :done )
set /a N+=1
if %N% GEQ 20 ( echo [frontend] Not ready in 40s, see logs-frontend.txt & goto :done )
timeout /t 2 /nobreak >nul
goto :wait_fe

:done
echo.
echo ==============================================
echo   Admin UI:   http://localhost:9000
echo   Default:    admin / admin123
echo   API Docs:   http://localhost:8089/docs
echo   Stop all:   stop-all.bat
echo ==============================================
endlocal
