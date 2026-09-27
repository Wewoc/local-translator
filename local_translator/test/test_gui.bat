@echo off
setlocal
title LocalTranslate — Batch Quality Test (GUI)
echo.
echo  LocalTranslate — Batch Quality Test GUI
echo.

:: Python check
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    pause
    exit /b 1
)

:: Run — server reachability is checked inside the GUI itself
cd /d "%~dp0"
python test_gui.py

pause
