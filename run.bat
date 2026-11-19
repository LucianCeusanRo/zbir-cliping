@echo off
REM Porneste aplicatia Zbir Cliping (la fel ca scurtatura de pe Desktop)
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (echo Ruleaza intai setup.bat & pause & exit /b 1)
powercfg /change standby-timeout-ac 0 >nul 2>nul
start "" .venv\Scripts\pythonw.exe -m zbirclip app
