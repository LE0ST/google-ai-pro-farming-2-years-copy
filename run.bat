@echo off
rem Start the local read-only diagnostics console through the PowerShell wrapper.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %ERRORLEVEL%
