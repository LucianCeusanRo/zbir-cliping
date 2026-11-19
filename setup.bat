@echo off
REM ================================================================
REM  Zbir Cliping - instalare (o singura data). Fara winget, fara "py",
REM  fara drepturi de administrator. Merge din orice folder/disc,
REM  si cu spatii in cale (ex. "D:\Programe mele\zbir-clipper").
REM ================================================================
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "APP=%~dp0"
set "APPD=%~dp0."
set "BIN=%~dp0bin"
set "HELP=%~dp0tools\setup_helpers.ps1"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
set "DL=%TEMP%\zbir_setup"
if not exist "%DL%" mkdir "%DL%"
if not exist "%BIN%" mkdir "%BIN%"
REM programs downloaded by setup (ffmpeg, deno) live in bin\ -> visible to everything started from here
set "PATH=%BIN%;%PATH%"

echo ================================================
echo   ZBIR CLIPING - instalare (o singura data)
echo   Folder: %APP%
echo ================================================

REM ---------------------------------------------------------------
REM  1) Python 3.12 (sau 3.11 / 3.13)
REM ---------------------------------------------------------------
set "PY="
call :find_python
if defined PY goto have_python

echo [*] Nu gasesc Python pe PC. Descarc Python 3.12 oficial (python.org, ~27 MB)...
set "PYINST=%DL%\python-3.12.10-amd64.exe"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HELP%" -Action download -Url "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" -Out "%PYINST%"
if errorlevel 1 goto fail_python_dl
echo [*] Instalez Python 3.12 doar pentru utilizatorul tau (fara admin, 1-3 minute)...
start "" /wait "%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0
REM chiar daca instalatorul raspunde "Another version is already installed" (0x80070666), cautam din nou
call :find_python
if defined PY goto have_python
goto fail_python

:have_python
echo [OK] Python: %PY%
"%PY%" -V

REM ---------------------------------------------------------------
REM  2) ffmpeg (cu NVENC) -> bin\
REM ---------------------------------------------------------------
if exist "%BIN%\ffmpeg.exe" if exist "%BIN%\ffprobe.exe" goto have_ffmpeg
echo [*] Descarc ffmpeg (gyan.dev, ~115 MB, cu NVENC pentru placa NVIDIA)...
set "FFZ=%DL%\ffmpeg.zip"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HELP%" -Action download -Url "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -Out "%FFZ%"
if not errorlevel 1 goto ffmpeg_unzip
echo [*] gyan.dev nu merge, incerc GitHub (BtbN, ~200 MB)...
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HELP%" -Action download -Url "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -Out "%FFZ%"
if errorlevel 1 goto fail_ffmpeg
:ffmpeg_unzip
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HELP%" -Action extract -Zip "%FFZ%" -Dest "%BIN%" -Names "ffmpeg.exe,ffprobe.exe"
if errorlevel 1 goto fail_ffmpeg
del /q "%FFZ%" >nul 2>nul
:have_ffmpeg
echo [OK] ffmpeg: %BIN%\ffmpeg.exe

REM ---------------------------------------------------------------
REM  3) Deno (yt-dlp are nevoie de un runtime JavaScript pentru YouTube) -> bin\
REM ---------------------------------------------------------------
if exist "%BIN%\deno.exe" goto have_deno
echo [*] Descarc Deno (GitHub, ~40 MB)...
set "DZ=%DL%\deno.zip"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HELP%" -Action download -Url "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" -Out "%DZ%"
if errorlevel 1 goto warn_deno
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HELP%" -Action extract -Zip "%DZ%" -Dest "%BIN%" -Names "deno.exe"
if errorlevel 1 goto warn_deno
del /q "%DZ%" >nul 2>nul
:have_deno
echo [OK] Deno: %BIN%\deno.exe
goto after_deno
:warn_deno
echo [!] Deno nu s-a putut descarca. Programul merge, dar YouTube poate refuza unele live-uri. Ruleaza setup.bat din nou mai tarziu.
:after_deno

REM ---------------------------------------------------------------
REM  4) mediul Python al programului (.venv) + pachete
REM ---------------------------------------------------------------
set "VPY=%APP%.venv\Scripts\python.exe"
if not exist "%VPY%" goto make_venv
"%VPY%" -c "import sys" >nul 2>nul
if not errorlevel 1 goto have_venv
echo [*] .venv stricat (alt Python?) - il refac...
rmdir /s /q "%APP%.venv"
:make_venv
echo [*] Creez mediul Python (.venv)...
"%PY%" -m venv "%APP%.venv"
if errorlevel 1 goto fail_venv
:have_venv
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r "%APP%requirements.txt"
if not errorlevel 1 goto pip_ok
echo [*] Reincerc instalarea pachetelor o data...
"%VPY%" -m pip install -r "%APP%requirements.txt"
if errorlevel 1 goto fail_pip
:pip_ok
echo [*] Instalez browserul pentru postare (Chromium)...
"%VPY%" -m playwright install chromium
if errorlevel 1 echo [!] Chromium nu s-a instalat - programul va folosi Google Chrome daca il ai.

echo [*] Descarc modelul Whisper large-v3 (~3 GB, o singura data)...
"%VPY%" -c "from zbirclip.util import setup_cuda_dlls; setup_cuda_dlls(); from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8'); print('model OK')"
if errorlevel 1 echo [!] Modelul nu s-a descarcat acum - se descarca automat la prima pornire.

echo [*] Verific placa video (CUDA + NVENC)...
"%VPY%" -c "from zbirclip.util import setup_cuda_dlls; setup_cuda_dlls(); import ctranslate2; print('GPU-uri CUDA:', ctranslate2.get_cuda_device_count())"
"%BIN%\ffmpeg.exe" -v error -f lavfi -i color=black:s=256x256:d=0.5 -c:v h264_nvenc -f null - >nul 2>nul
if errorlevel 1 goto no_nvenc
echo [OK] NVENC merge
goto after_nvenc
:no_nvenc
echo [!] NVENC indisponibil - se foloseste procesorul, mai lent
:after_nvenc

echo [*] Verific ca totul merge (test automat)...
"%VPY%" -m zbirclip selftest --quick
if errorlevel 1 echo [!] Unele teste au picat - trimite-mi fereastra asta.

echo [*] Creez scurtatura "Zbir Cliping" pe Desktop...
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HELP%" -Action shortcut -Target "%APP%Zbir Cliping.exe" -WorkDir "%APPD%" -Icon "%APP%assets\icon.ico"
if errorlevel 1 echo [!] Nu am putut crea scurtatura - porneste direct "Zbir Cliping.exe" din folder.

echo.
echo ================================================
echo  GATA! Deschide "Zbir Cliping" de pe Desktop.
echo  Prima data: apasa "Login TikTok" in aplicatie.
echo ================================================
pause
exit /b 0

REM ===============================================================
REM  Cautare Python: registru, caile obisnuite, "py", "where python"
REM ===============================================================
:find_python
set "PY="
REM registry (python.org installers always write this, even when Python is not on PATH)
for %%V in (3.12 3.11 3.13) do call :try_reg_ver %%V
if defined PY exit /b 0
call :try_py "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try_py "%ProgramFiles%\Python312\python.exe"
call :try_py "C:\Program Files\Python312\python.exe"
call :try_py "C:\Python312\python.exe"
call :try_py "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try_py "%ProgramFiles%\Python311\python.exe"
call :try_py "C:\Python311\python.exe"
call :try_py "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try_py "%ProgramFiles%\Python313\python.exe"
call :try_py "C:\Python313\python.exe"
if defined PY exit /b 0
REM py launcher, if present
for /f "usebackq delims=" %%P in (`py -3.12 -c "import sys;print(sys.executable)" 2^>nul`) do call :try_py "%%P"
if defined PY exit /b 0
REM anything called python.exe on PATH (skips the Microsoft Store stub)
for /f "usebackq delims=" %%P in (`where python 2^>nul`) do call :try_py "%%P"
exit /b 0

:try_reg_ver
call :try_reg "HKCU\Software\Python\PythonCore\%~1\InstallPath"
call :try_reg "HKLM\Software\Python\PythonCore\%~1\InstallPath"
call :try_reg "HKLM\Software\WOW6432Node\Python\PythonCore\%~1\InstallPath"
exit /b 0

:try_reg
REM %1 = registry key. Reads ExecutablePath, else the default value (= folder). Works on any Windows language.
if defined PY exit /b 0
set "RP="
for /f "delims=" %%L in ('reg query "%~1" /v ExecutablePath 2^>nul ^| findstr /c:"REG_SZ"') do set "RL=%%L"& call :regval
if defined RP call :try_py "%RP%"
if defined PY exit /b 0
set "RP="
for /f "delims=" %%L in ('reg query "%~1" /ve 2^>nul ^| findstr /c:"REG_SZ"') do set "RL=%%L"& call :regval
if not defined RP exit /b 0
if "%RP:~-1%"=="\" set "RP=%RP:~0,-1%"
call :try_py "%RP%\python.exe"
exit /b 0

:regval
REM keep what comes after "REG_SZ", without the leading spaces/tabs
set "RP=%RL:*REG_SZ=%"
:regval_trim
if not defined RP exit /b 0
if "%RP:~0,1%"==" " set "RP=%RP:~1%"& goto regval_trim
if "%RP:~0,1%"=="	" set "RP=%RP:~1%"& goto regval_trim
exit /b 0

:try_py
if defined PY exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
echo "%~1" | findstr /i "WindowsApps" >nul && exit /b 0
"%~1" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0

REM ===============================================================
:fail_python_dl
echo [!] Nu am putut descarca Python. Descarca-l manual de pe https://www.python.org/downloads/release/python-31210/
echo     (Windows installer 64-bit), instaleaza-l, apoi ruleaza din nou setup.bat.
pause & exit /b 1
:fail_python
echo [!] Python s-a instalat, dar nu-l gasesc. Ruleaza din nou setup.bat (poate e nevoie de o repornire a PC-ului).
pause & exit /b 1
:fail_ffmpeg
echo [!] Nu am putut descarca ffmpeg. Verifica internetul/antivirusul si ruleaza din nou setup.bat.
pause & exit /b 1
:fail_venv
echo [!] Nu pot crea mediul Python (.venv) cu %PY%
pause & exit /b 1
:fail_pip
echo [!] Instalarea pachetelor a esuat (vezi mesajele de mai sus). Verifica internetul si ruleaza din nou setup.bat.
pause & exit /b 1
