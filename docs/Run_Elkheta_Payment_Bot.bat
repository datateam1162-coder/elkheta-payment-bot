@echo off
chcp 65001 >nul
title Elkheta Auto-Payment System - Employee Launcher
echo ============================================================
echo    سيستم تسجيل مدفوعات الخطة الذكي (Elkheta Payment Bot)
echo ============================================================
echo.

if exist "%~dp0app.py" (
    cd /d "%~dp0"
    goto :install_and_run
)

set "TARGET_DIR=%USERPROFILE%\Elkheta_Payment_Bot"
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"
cd /d "%TARGET_DIR%"

if exist "%TARGET_DIR%\.git" (
    echo [*] جاري تحديث السيستم من GitHub...
    git pull origin main >nul 2>&1
) else (
    where git >nul 2>&1
    if %errorlevel% equ 0 (
        echo [*] جاري تحميل السيستم من GitHub...
        git clone https://github.com/datateam1162-coder/elkheta-payment-bot.git "%TARGET_DIR%"
    ) else (
        echo [*] جاري تحميل ملفات السيستم مباشرة من GitHub...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://github.com/datateam1162-coder/elkheta-payment-bot/archive/refs/heads/main.zip' -OutFile 'bot.zip'; Expand-Archive -Path 'bot.zip' -DestinationPath '.' -Force; Copy-Item -Path 'elkheta-payment-bot-main\*' -Destination '.' -Recurse -Force"
    )
)

:install_and_run
echo [*] جاري التأكد من المكتبات المطلوبة (Flask + Playwright)...
python -m pip install flask requests playwright --quiet 2>nul
echo [*] جاري تشغيل السيرفر المحلي وفتح المتصفح على http://127.0.0.1:5055 ...
start "" "http://127.0.0.1:5055"
python app.py
pause
