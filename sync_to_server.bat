@echo off
title Sync Token ^& Start Axiom on Server
echo ============================================================
echo          SYNCING WITH ORACLE CLOUD SERVER (140.245.232.60)
echo ============================================================
echo.

echo [1/4] Fetching daily Flattrade request_code via Playwright...
py -3.13 "%~dp0token_helper\get_token.py" 2>nul || python "%~dp0token_helper\get_token.py"
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Token retrieval had an issue. Continuing to upload existing .env...
)

echo.
echo [2/4] Uploading fresh .env and token map to server...
scp -o StrictHostKeyChecking=no -i "%~dp0ssh-key-2026-09-12.key" "%~dp0.env" opc@140.245.232.60:/home/opc/Axiom/.env
scp -o StrictHostKeyChecking=no -i "%~dp0ssh-key-2026-09-12.key" "%~dp0data\token_map.json" opc@140.245.232.60:/home/opc/Axiom/data/token_map.json
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to upload configuration.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [3/4] Restarting Axiom on server...
ssh -o StrictHostKeyChecking=no -i "%~dp0ssh-key-2026-09-12.key" opc@140.245.232.60 "/home/opc/Axiom/stop.sh; /home/opc/Axiom/start.sh"

echo.
echo [4/4] Checking server status...
ssh -o StrictHostKeyChecking=no -i "%~dp0ssh-key-2026-09-12.key" opc@140.245.232.60 "/home/opc/Axiom/status.sh"

echo.
echo ============================================================
echo Axiom is synced and running on Oracle Cloud!
echo ============================================================
pause
