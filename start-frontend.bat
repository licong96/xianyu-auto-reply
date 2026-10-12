@echo off
cd /d "%~dp0"
start "xianyu-frontend" /min cmd /c "cd frontend && npm run dev > ..\logs-frontend.txt 2>&1"
