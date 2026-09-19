@echo off
rem ============================================================
rem  build_exe.bat - one-click build for SC2 Uncensor (PyInstaller onedir)
rem  Double-click this file, or run it from a cmd window.
rem  ASCII-only on purpose (see app\sc2_uncensor.bat for the reason).
rem  Do NOT use parenthesised "if ( ... )" blocks here: a ")" inside an
rem  echo line would end the block early and cmd would die.
rem ============================================================
title SC2 Uncensor - build exe
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto nopy

set "ARGS=--zip"
set "SELFTEST="
echo.
echo Type "y" then Enter to ALSO build+run the frozen self-check (Enter = skip):
set /p SELFTEST=
if /i "%SELFTEST%"=="y" set "ARGS=%ARGS% --selftest"
if /i "%~1"=="full" set "ARGS=--zip --console --selftest"
if /i "%~1"=="selftest" set "ARGS=--zip --selftest"

if /i "%~1"=="notest" set "ARGS=--zip --skip-tests"

if /i "%~1"=="notest-full" set "ARGS=--zip --selftest --skip-tests"
echo Building with: %ARGS%
".venv\Scripts\python.exe" scripts\build_exe.py %ARGS%
echo.
echo Done. Output is in the dist\ folder (see the summary above).
pause
exit /b 0

:nopy
echo [ERROR] Python venv not found: .venv\Scripts\python.exe
echo         This script must sit in the project root (next to the .venv folder).
echo         If PyInstaller is missing:
echo             .venv\Scripts\python.exe -m pip install pyinstaller
pause
exit /b 1
