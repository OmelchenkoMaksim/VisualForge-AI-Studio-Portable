@echo off
rem Installs or completes the programs and (optionally) the models of VisualForge AI Studio. Safe to run again at any time.
rem   Setup-VisualForge.cmd                 menu
rem   Setup-VisualForge.cmd --software-only programs only, models later
rem   Setup-VisualForge.cmd --help          all options
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0App\Setup-Bootstrap.ps1" %*
set "code=%ERRORLEVEL%"
if not "%code%"=="0" if not "%code%"=="130" echo.& echo Setup finished with code %code%. Run it again to continue; finished downloads are kept.
if "%VISUALFORGE_NO_PAUSE%"=="" pause
exit /b %code%
