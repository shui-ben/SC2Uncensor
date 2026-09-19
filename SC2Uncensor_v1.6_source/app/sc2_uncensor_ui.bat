@echo off
rem sc2_uncensor_ui.bat - SC2 Uncensor, graphical UI launcher (source-tree version).
rem
rem ASCII-only on purpose: batch files must not depend on the console codepage.
rem NOTE: do NOT use parenthesised "if ( ... )" blocks here. Inside such a block an
rem       ")" in an echo line ends the block early and cmd dies with
rem       "The syntax of the command is incorrect" and the window just
rem       flashes away. Use labels and goto instead, like sc2_uncensor.bat does.
rem
rem This launcher is intentionally dumb: it only runs the UI with pythonw so there is
rem no console window. Everything else - including "run as administrator" - is handled
rem by the UI itself: config.json "admin": true makes the UI ask for UAC and relaunch
rem itself elevated. The console launcher sc2_uncensor.bat is untouched.
title SC2 Uncensor UI
cd /d "%~dp0.."
if exist ".venv\Scripts\pythonw.exe" goto run
echo [ERROR] Python venv not found: .venv\Scripts\pythonw.exe
echo         Please run this from the project root, the venv must exist.
pause
exit /b 1
:run
start "" ".venv\Scripts\pythonw.exe" "app\ui_main.py"
