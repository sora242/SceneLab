@echo off
title SceneLab
cd /d "%~dp0app"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python and enable "Add python.exe to PATH".
  pause
  exit /b 1
)
python app.py
pause
