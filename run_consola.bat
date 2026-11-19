@echo off
chcp 65001 >nul
title Zbir Cliping - consola (pentru depanare)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
powercfg /change standby-timeout-ac 0 >nul 2>nul
:loop
python -m zbirclip app
echo Aplicatia s-a oprit. Repornesc in 30 de secunde (Ctrl+C ca sa opresti de tot)...
timeout /t 30
goto loop
