@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === Editor de video: instalacao ===

where winget >nul 2>nul || (echo winget nao encontrado. Instale o "App Installer" pela Microsoft Store. & pause & exit /b 1)

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
  echo Instalando Python 3.12...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
)
if not exist "%PY%" (echo Nao foi possivel instalar o Python. & pause & exit /b 1)

where ffmpeg >nul 2>nul
if errorlevel 1 if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe" (
  echo Instalando FFmpeg...
  winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
)

if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente Python...
  "%PY%" -m venv .venv || (pause & exit /b 1)
)
echo Instalando bibliotecas (faster-whisper, OpenCV)...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || (pause & exit /b 1)

echo.
echo Pronto! Rode iniciar.bat para abrir o editor.
echo (Na primeira transcricao o modelo Whisper, ~480 MB, e baixado uma unica vez.)
pause
