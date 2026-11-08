@echo off
rem Pembungkus tipis: semua logika ada di run.ps1 (padanan run.sh).
rem Bypass ExecutionPolicy hanya untuk proses ini — tidak mengubah sistem.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %ERRORLEVEL%
