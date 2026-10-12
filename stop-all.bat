@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "ROOT=%cd%"

echo ==============================================
echo   Xianyu Auto-Reply - Stop All Services
echo   Root: %ROOT%
echo ==============================================

rem stop_port ^<port^>: kill the process listening on the port,
rem only if it belongs to this project (command line contains project root
rem or vite/node_modules markers), to avoid killing foreign programs.
goto :main

:stop_port
set "PORT=%~1"
set "PID="
for /f %%P in ('powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { $c.OwningProcess }" 2^>nul') do set "PID=%%P"
if not defined PID (
    echo [port %PORT%] Not running
    exit /b 0
)

set "CL="
for /f "usebackq delims=" %%C in (`powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter 'ProcessId=%PID%').CommandLine" 2^>nul`) do set "CL=%%C"

set "OWN=0"
echo(!CL! | findstr /I /C:"%ROOT%" >nul && set "OWN=1"
if "%OWN%"=="0" if not "%PORT%"=="9000" echo(!CL! | findstr /I /C:"main.py" >nul && set "OWN=1"
if "%OWN%"=="0" echo(!CL! | findstr /I /C:"vite" /C:"xianyu-admin-frontend" >nul && set "OWN=1"

if "%OWN%"=="0" (
    echo [port %PORT%] PID=%PID% does NOT belong to this project, skipped.
    echo             CommandLine: !CL!
    exit /b 0
)

rem /T also kills child processes (npm -^> node, python subprocesses)
taskkill /F /T /PID %PID% >nul 2>&1
if !errorlevel! equ 0 (
    echo [port %PORT%] Stopped PID=%PID%
) else (
    echo [port %PORT%] Failed to stop PID=%PID% (already exited or no permission)
)
exit /b 0

:main
for %%P in (8089 8090 8091 9000) do call :stop_port %%P

rem Fallback: sweep leftover python processes started from this project root
rem (e.g. crashed-but-alive workers without a listening port).
for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*%ROOT%*main.py*' } | Select-Object -ExpandProperty ProcessId" 2^>nul`) do (
    echo [sweep] Stopping leftover python PID=%%L
    taskkill /F /T /PID %%L >nul 2>&1
)

echo.
echo All application services stopped.
echo (MySQL / Redis are left running on purpose.)
endlocal
