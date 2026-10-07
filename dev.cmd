@echo off
rem SmartLogistics developer CLI for Windows cmd.exe -- forwards to dev.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
exit /b %ERRORLEVEL%
