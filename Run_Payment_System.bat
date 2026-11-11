@echo off
chcp 65001 >nul
title Elkheta Auto-Payment System
cd /d "%~dp0"
echo ====================================================
echo   Starting Elkheta Auto-Payment System...
echo   URL: http://127.0.0.1:5055
echo ====================================================
start "" http://127.0.0.1:5055
python app.py
pause
