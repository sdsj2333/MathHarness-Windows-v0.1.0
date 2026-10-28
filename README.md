# Math Harness for Windows

A local workbench for drafting and reviewing mathematical proofs. It supports a ChatGPT account through the official Codex CLI, or a configurable OpenAI-compatible Chat Completions API.

**[中文使用说明](README-zh.md)**

## Quick start

1. Install Python 3.11 or newer on Windows.
2. Download the source and run `start-windows.bat`. The script creates a local virtual environment and installs the desktop dependency.
3. For ChatGPT account mode, install the official Codex CLI, sign in with your own account, and use **Models and Account → Check Status → Test Account Connection** in the app.

The app stores research records under `%LOCALAPPDATA%\MathHarness` by default. Model output and review findings require human verification. This source release does not include a prebuilt executable.
