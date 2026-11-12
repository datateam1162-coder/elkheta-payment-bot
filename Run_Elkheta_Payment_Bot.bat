@echo off
title Elkheta Auto-Payment System
echo ============================================================
echo    Elkheta Auto-Payment System - One-Click Auto Launcher
echo ============================================================
echo.

if exist "%~dp0app.py" goto :run_current
if exist "C:\Users\ROOT\.gemini\antigravity\scratch\elkheta-payment-bot\app.py" goto :run_scratch

:: 1. Ensure Python is installed on the employee's PC (auto-install silently if missing)
python --version >nul 2>&1
if %errorlevel% equ 0 goto :check_files

echo [*] Python is not installed on this PC. Installing Python automatically (please wait 1 minute)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '$env:TEMP\python_setup.exe'; Start-Process -FilePath '$env:TEMP\python_setup.exe' -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_test=0' -Wait"
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"

:check_files
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
echo [*] Preparing required libraries automatically...
python -m pip install --upgrade pip --quiet >nul 2>&1
python -m pip install flask requests playwright --quiet

:start_server
echo [*] Launching http://127.0.0.1:5055 ...
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:5055'"
python app.py
pause
