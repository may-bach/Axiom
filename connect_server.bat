@echo off
title Connect to Axiom Cloud Server
echo Connecting to Oracle Cloud Server (140.245.232.60)...
echo.
ssh -i "%~dp0ssh-key-2026-09-12.key" opc@140.245.232.60
pause
