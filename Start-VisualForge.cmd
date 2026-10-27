@echo off
rem Starts VisualForge AI Studio. On the very first start the programs are installed first (see Setup-VisualForge.cmd).
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "Runtime\ComfyUI_windows_portable\python_embeded\python.exe" (
    echo First start: the programs around the neural networks are not installed yet.
    set "VISUALFORGE_NO_PAUSE=1"
    call "%~dp0Setup-VisualForge.cmd"
    if errorlevel 1 (
        echo.
        echo The installation did not finish. Run Setup-VisualForge.cmd again.
        pause
        exit /b 1
    )
)
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0App\Start-VisualForge.ps1"
exit /b 0
