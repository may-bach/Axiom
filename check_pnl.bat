@echo off
title Axiom - P^&L Report
echo Connecting to Oracle Cloud Server (140.245.232.60)...
echo.
ssh -o StrictHostKeyChecking=no -i ssh-key-2026-09-12.key opc@140.245.232.60 "/home/opc/Axiom/pnl.sh"
echo.
pause
