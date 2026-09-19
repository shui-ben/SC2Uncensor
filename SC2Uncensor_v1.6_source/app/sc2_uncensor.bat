@echo off
rem sc2_uncensor.bat - SC2 CN Uncensor one-key tool
rem Elevation is OPTIONAL: config.json "admin": true -> auto UAC prompt on start.
rem Default false (SC2 usually runs non-elevated; tool works without admin then).
rem NOTE: ASCII-only on purpose. Batch files must not depend on console codepage
rem       (chcp 65001 + multibyte echo lines can desync the cmd parser = flash exit).
rem
rem Game-version check: on attach the tool reads the SC2 exe version and refuses
rem   memory ops on untested versions (prevents corrupting other builds).
rem   Tested builds: V5.0.15 / V5.0.16 (64-bit, CN client).
rem   Small updates usually keep the same offsets - to skip the check, set
rem   "check_version": false in app\config.json and try at your own risk.
rem   New offsets for updated game versions: see offsets.dat + repo docs.
title SC2 Uncensor
cd /d "%~dp0.."
set "NEED_ADMIN="
for /f %%i in ('.venv\Scripts\python.exe app\admin_flag.py 2^>nul') do set "NEED_ADMIN=%%i"
net session >nul 2>&1
if %errorlevel% equ 0 goto :run
if /i not "%NEED_ADMIN%"=="True" goto :run
echo [UAC] admin=true: requesting administrator privileges, please click YES...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
if %errorlevel% neq 0 (
    echo [UAC] Declined. Set "admin": false in app config.json, or click YES next time.
    pause
)
exit /b
:run
set "PYTHONIOENCODING=utf-8"
.venv\Scripts\python.exe app\sc2_uncensor.py
echo.
pause
