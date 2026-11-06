@echo off
setlocal
cd /d "%~dp0"
if exist "runtime\python\python.exe" (
  "runtime\python\python.exe" launch.py
) else (
  python launch.py
)
if errorlevel 1 pause
