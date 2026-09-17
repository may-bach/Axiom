@echo off
title Axiom Intraday Trading Bot
echo ============================================================
echo               AXIOM INTRADAY TRADING BOT
echo ============================================================
echo.

echo [1/2] Fetching daily Flattrade request_code via Playwright...
py -3.13 token_helper\get_token.py 2>nul || python token_helper\get_token.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Token retrieval failed. Check credentials or screenshots in token_helper.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/2] Launching Axiom Engine...
go run ./cmd
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Axiom exited with an error.
    pause
    exit /b %ERRORLEVEL%
)

pause
