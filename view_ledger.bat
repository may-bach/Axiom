@echo off
title Axiom - Multi-Week Audit Ledger
echo Connecting to Oracle Cloud Server (140.245.232.60)...
echo Updating and generating latest multi-week audit ledger...
echo.

:: 1. Run ledger.py on server to update analysis
ssh -o StrictHostKeyChecking=no -i ssh-key-2026-09-12.key opc@140.245.232.60 "python3 /home/opc/Axiom/ledger.py"

:: 2. Download the latest CSV, Markdown, and HTML files locally
if not exist "data" mkdir data
scp -o StrictHostKeyChecking=no -i ssh-key-2026-09-12.key opc@140.245.232.60:/home/opc/Axiom/data/trades_ledger.csv data/trades_ledger.csv >nul 2>&1
scp -o StrictHostKeyChecking=no -i ssh-key-2026-09-12.key opc@140.245.232.60:/home/opc/Axiom/data/ledger_summary.md data/ledger_summary.md >nul 2>&1
scp -o StrictHostKeyChecking=no -i ssh-key-2026-09-12.key opc@140.245.232.60:/home/opc/Axiom/data/ledger.html data/ledger.html >nul 2>&1

echo.
echo =================================================================
echo [OK] Audit files synced locally to your machine:
echo   - Excel / CSV Spreadsheet : data\trades_ledger.csv
echo   - Markdown Audit Report   : data\ledger_summary.md
echo   - Visual Dashboard        : data\ledger.html
echo =================================================================
echo.

:: 3. Automatically open visual dashboard in browser
if exist "data\ledger.html" (
    start "" "data\ledger.html"
)

pause
