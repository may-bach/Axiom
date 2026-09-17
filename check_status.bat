@echo off
title Axiom - Cloud Server Status
echo Connecting to Oracle Cloud Server (140.245.232.60)...
echo.
ssh -o StrictHostKeyChecking=no -i ssh-key-2026-09-12.key opc@140.245.232.60 "/home/opc/Axiom/status.sh"
echo.
pause
