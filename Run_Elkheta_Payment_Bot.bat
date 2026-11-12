@echo off
title Elkheta Auto-Payment System
echo ============================================================
echo    Elkheta Auto-Payment System - Starting Local Engine...
echo ============================================================
echo.

if exist "%~dp0app.py" goto :run_current
if exist "C:\Users\ROOT\.gemini\antigravity\scratch\elkheta-payment-bot\app.py" goto :run_scratch

set "TARGET_DIR=%USERPROFILE%\Elkheta_Payment_Bot"
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"
cd /d "%TARGET_DIR%"

if exist "%TARGET_DIR%\app.py" goto :update_existing

echo [*] Downloading Elkheta Payment Bot from GitHub...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://github.com/datateam1162-coder/elkheta-payment-bot/archive/refs/heads/main.zip' -OutFile 'bot.zip'; Expand-Archive -Path 'bot.zip' -DestinationPath '.' -Force; Copy-Item -Path 'elkheta-payment-bot-main\*' -Destination '.' -Recurse -Force"
goto :install_deps

:update_existing
if exist "%TARGET_DIR%\.git" git pull origin main >nul 2>&1
goto :install_deps

:run_current
cd /d "%~dp0"
goto :start_server

:run_scratch
cd /d "C:\Users\ROOT\.gemini\antigravity\scratch\elkheta-payment-bot"
goto :start_server

:install_deps
echo [*] Installing required Python packages (Flask + Playwright)...
python -m pip install flask requests playwright --quiet
python -m playwright install chromium

:start_server
echo [*] Launching http://127.0.0.1:5055 ...
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:5055'"
python app.py
pause
