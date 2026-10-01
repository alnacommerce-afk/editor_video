@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Rode instalar.bat primeiro. & pause & exit /b 1)
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m editor.server
pause
