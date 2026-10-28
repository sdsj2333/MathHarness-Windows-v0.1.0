@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_CMD=py -3"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Please install Python 3.11 or newer, then run this file again.
    pause
    exit /b 1
  )
  set "PYTHON_CMD=python"
)
if not exist ".venv\Scripts\python.exe" (
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -e ".[desktop]"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m math_harness
if errorlevel 1 goto failed
exit /b 0
:failed
echo Installation or startup failed. Please see README-zh.md.
pause
exit /b 1
