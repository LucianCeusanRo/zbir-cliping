@echo off
REM Reconstruieste "Zbir Cliping.exe" (lansatorul cu iconita) pe Windows. Nu e obligatoriu: exe-ul vine gata facut.
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (echo [!] Ruleaza intai setup.bat & pause & exit /b 1)
"%PY%" -m pip install --quiet ziglang || (echo [!] nu pot instala ziglang & pause & exit /b 1)
cd launcher
"%PY%" -m ziglang cc -target x86_64-windows-gnu -O2 -s -municode -Wl,--subsystem,windows launcher.c launcher.rc -o "..\Zbir Cliping.exe" -lshell32 -luser32 || (echo [!] build esuat & pause & exit /b 1)
cd ..
del /q *.pdb 2>nul
echo GATA: "Zbir Cliping.exe"
pause
