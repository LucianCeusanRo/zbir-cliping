@echo off
REM Actualizeaza Zbir Cliping de pe GitHub (fara git). Datele, setarile, contul TikTok, .venv si bin\ raman neatinse.
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (echo [!] Ruleaza intai setup.bat & pause & exit /b 1)
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8777/api/state) | Out-Null; exit 1 } catch { exit 0 }"
if errorlevel 1 (
  echo [!] Zbir Cliping e pornit. Inchide-l intai ^(sau foloseste butonul "Update" din aplicatie^).
  pause & exit /b 1
)
"%VPY%" -m zbirclip update %*
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
