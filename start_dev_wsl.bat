@echo off
setlocal

set "WSL_PROJECT_DIR=/mnt/c/Users/aryan/Downloads/Home-Vision-AI-main"

echo Starting Home Vision AI dev stack in WSL...
echo.

wsl.exe bash -lc "cd '%WSL_PROJECT_DIR%' && bash dev_wsl.sh"

echo.
echo Dev stack stopped. Press any key to close this window.
pause >nul
