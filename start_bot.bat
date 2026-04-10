@echo off
title Auto Trader 1000

echo ============================================
echo   Auto Trader 1000 — Starting up...
echo ============================================

:: Start MetaTrader 5
echo [1/3] Launching MetaTrader 5...
start "" "C:\Program Files\MetaTrader 5\terminal64.exe"

:: Wait for MT5 to initialize
echo [2/3] Waiting 15 seconds for MT5 to connect...
timeout /t 15 /nobreak >nul

:: Change to bot directory
cd /d "C:\Users\wel\AutoTrader 1000"

:: Start the dashboard in background
echo [3/3] Starting dashboard + bot...
start "AT1000-Dashboard" py dashboard.py

:: Start the bot (foreground — keeps window open)
py main.py

:: If bot exits, pause so user can see errors
echo.
echo Bot stopped. Press any key to close.
pause >nul
