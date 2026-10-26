@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\OEM\collect.ps1
exit /b %ERRORLEVEL%
