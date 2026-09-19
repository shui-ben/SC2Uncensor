@echo off
rem ============================================================
rem  build_exe_notest.bat - build WITHOUT the pre-build regression tests
rem  (kept as the "just give me a package" entry point)
rem  After the build it asks whether to run the frozen self-check.
rem ============================================================
title SC2 Uncensor - build exe (no tests)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto nopy
set "ARGS=--zip --skip-tests"
set "SELFTEST="
echo.
echo Type "y" then Enter to ALSO build+run the frozen self-check (Enter = skip):
set /p SELFTEST=
if /i "%SELFTEST%"=="y" set "ARGS=%ARGS% --selftest"
echo.
echo Building: %ARGS%
".venv\Scripts\python.exe" scripts\build_exe.py %ARGS%
echo.
echo Done. Output is in the dist\ folder (see the summary above).
pause
exit /b 0

:nopy
echo [ERROR] Python venv not found: .venv\Scripts\python.exe
echo         This script must sit in the project root (next to the .venv folder).
pause
exit /b 1
