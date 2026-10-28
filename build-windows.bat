@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start-windows.bat once before building.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -e ".[desktop,build]"
if errorlevel 1 goto failed
".venv\Scripts\pyinstaller.exe" --clean --noconfirm --onefile --windowed --name MathHarness --add-data "math_harness\static;math_harness\static" launcher.py
if errorlevel 1 goto failed
echo Build completed: dist\MathHarness.exe
pause
exit /b 0
:failed
echo Build failed. Review the messages above.
pause
exit /b 1
