@echo off
powershell.exe -ExecutionPolicy Bypass -File "%~dp0package_dist.ps1" %*
pause
