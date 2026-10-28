@echo off
setlocal
where codex >nul 2>nul
if not errorlevel 1 goto already_installed
if defined LOCALAPPDATA (
  for /d %%D in ("%LOCALAPPDATA%\OpenAI\Codex\bin\*") do (
    if exist "%%~fD\codex.exe" goto already_installed
  )
)
where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js and npm are required. Install Node.js LTS, then reopen this file.
  pause
  exit /b 1
)
npm install -g @openai/codex
if errorlevel 1 (
  echo Codex installation failed. Try installing it in a terminal to inspect the error.
  pause
  exit /b 1
)
echo Codex installed. Restart Math Harness, then use the GPT account login button.
pause
exit /b 0
:already_installed
echo Codex CLI is already installed. Close this window and run start-windows.bat.
echo In Math Harness, open Models and Account and click Check Status.
pause
