@echo off
title Protocol Buyer Lab
powershell.exe -NoProfile -File "%~dp0start_node.ps1" %*
if errorlevel 1 pause

