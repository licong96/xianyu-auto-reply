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
rem NOTE: subroutines called from inside parenthesized IF blocks MUST read
rem their inputs from preset variables (CHECK_PORT / WAIT_PORT / SVC_DIR),
rem because call-arguments (%1/%2) are unreliable in that context.

goto :main

rem is_healthy: uses CHECK_PORT, exits 0 when /health returns HTTP 200 + success
:is_healthy
powershell -NoProfile -Command "try { $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:%CHECK_PORT%/health' -TimeoutSec 3 -UseBasicParsing; if ($resp.StatusCode -eq 200 -and $resp.Content -match 'success') { exit 0 } else { exit 1 } } catch { exit 1 }"
exit /b %errorlevel%

rem port_pid: uses CHECK_PORT, sets PID to the listener PID (empty if none)
:port_pid
set "PID="
for /f %%P in ('powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %CHECK_PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { $c.OwningProcess }" 2^>nul') do (
    echo(%%P|findstr /r "[0-9][0-9]*" >nul && set "PID=%%P"
)
exit /b 0

rem wait_healthy: uses WAIT_PORT / WAIT_SECS
:wait_healthy
set /a TRIES=%WAIT_SECS%/2
if %TRIES% LSS 1 set TRIES=1
set "CHECK_PORT=%WAIT_PORT%"
for /l %%I in (1,1,%TRIES%) do (
    call :is_healthy
    if !errorlevel! equ 0 exit /b 0
    ping -n 3 127.0.0.1 >nul
)
exit /b 1

rem start_py: uses SVC_DIR, starts a python service hidden and detached
:start_py
echo [%SVC_NAME%] Starting...
powershell -NoProfile -Command "Start-Process -FilePath '%ROOT%\%SVC_DIR%\.venv\Scripts\python.exe' -ArgumentList 'main.py' -WorkingDirectory '%ROOT%\%SVC_DIR%' -WindowStyle Hidden -RedirectStandardOutput '%ROOT%\%SVC_DIR%\logs_start.log' -RedirectStandardError '%ROOT%\%SVC_DIR%\logs_start.err.log'"
exit /b 0

rem ---------- 3. Main ----------

:main

rem 3.1 backend-web (8089) - others depend on it
set "CHECK_PORT=8089"
call :is_healthy
if %errorlevel% equ 0 (
    echo [backend-web] Already running on port 8089
) else (
    set "CHECK_PORT=8089"
    call :port_pid
    if defined PID (
        echo [backend-web] Port 8089 occupied by PID=!PID! but unhealthy.
        echo             Run stop-all.bat first.
    ) else (
        set "SVC_NAME=backend-web"
        set "SVC_DIR=backend-web"
        call :start_py
        set "WAIT_PORT=8089"
        set "WAIT_SECS=120"
        call :wait_healthy
        if !errorlevel! equ 0 ( echo [backend-web] Ready on port 8089 [OK] ) else ( echo [backend-web] Not ready in 120s, see backend-web\logs_start.log )
    )
)

rem 3.2 websocket (8090)
set "CHECK_PORT=8090"
call :is_healthy
if %errorlevel% equ 0 (
    echo [websocket] Already running on port 8090
) else (
    set "CHECK_PORT=8090"
    call :port_pid
    if defined PID (
        echo [websocket] Port 8090 occupied by PID=!PID! but unhealthy. Run stop-all.bat first.
    ) else (
        set "SVC_NAME=websocket"
        set "SVC_DIR=websocket"
        call :start_py
        set "WAIT_PORT=8090"
        set "WAIT_SECS=90"
        call :wait_healthy
        if !errorlevel! equ 0 ( echo [websocket] Ready on port 8090 [OK] ) else ( echo [websocket] Not ready in 90s, see websocket\logs_start.log )
    )
)

rem 3.3 scheduler (8091)
set "CHECK_PORT=8091"
call :is_healthy
if %errorlevel% equ 0 (
    echo [scheduler] Already running on port 8091
) else (
    set "CHECK_PORT=8091"
    call :port_pid
    if defined PID (
        echo [scheduler] Port 8091 occupied by PID=!PID! but unhealthy. Run stop-all.bat first.
    ) else (
        set "SVC_NAME=scheduler"
        set "SVC_DIR=scheduler"
        call :start_py
        set "WAIT_PORT=8091"
        set "WAIT_SECS=90"
        call :wait_healthy
        if !errorlevel! equ 0 ( echo [scheduler] Ready on port 8091 [OK] ) else ( echo [scheduler] Not ready in 90s, see scheduler\logs_start.log )
    )
)

rem 3.4 frontend (9000, vite dev server)
set "CHECK_PORT=9000"
call :port_pid
if defined PID (
    echo [frontend] Already running on port 9000
) else (
    echo [frontend] Starting...
    powershell -NoProfile -Command "Start-Process -FilePath cmd.exe -ArgumentList '/c','npm run dev' -WorkingDirectory '%ROOT%\frontend' -WindowStyle Hidden -RedirectStandardOutput '%ROOT%\logs-frontend.txt' -RedirectStandardError '%ROOT%\logs-frontend.err.txt'"
)

rem 3.5 Wait for frontend
set /a N=0
:wait_fe
set "CHECK_PORT=9000"
call :port_pid
if defined PID ( echo [frontend] Ready on port 9000 [OK] & goto :done )
set /a N+=1
if %N% GEQ 20 ( echo [frontend] Not ready in 40s, see logs-frontend.txt & goto :done )
ping -n 3 127.0.0.1 >nul
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
