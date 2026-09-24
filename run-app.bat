@echo off
REM ---------------------------------------------------------------
REM  Launch the assistant (main.py).
REM
REM  Keys are read from the key folder at run time and only exported
REM  into the child process environment - nothing is written into the
REM  repo, and no key ever lands in a file or a log.
REM
REM  Set KEYDIR first if your key folder is somewhere else.
REM  To watch the console (tracebacks, etc.) run instead:
REM      .venv\Scripts\python.exe main.py
REM ---------------------------------------------------------------
setlocal

if not defined KEYDIR set "KEYDIR=E:\workdata\key"
if not exist "%KEYDIR%\jev.key"      ( echo [ERR] missing %KEYDIR%\jev.key & exit /b 1 )
if not exist "%KEYDIR%\opencode.key" ( echo [ERR] missing %KEYDIR%\opencode.key & exit /b 1 )

set "JEVC_KEY="
set "OC_KEY="
set /p JEVC_KEY=<"%KEYDIR%\jev.key"
set /p OC_KEY=<"%KEYDIR%\opencode.key"

REM ---- judge layer: hosted Jev proxy (the key below is issued by it) ----
set "JEVC_JUDGE_URL=https://jevtypesafeai.com/api/v1/decide"
set "JEVC_JUDGE_MODEL=jev-latest"

REM ---- OPENROUTER_API_KEY is the hard-coded name the judge layer reads ----
set "OPENROUTER_API_KEY=%JEVC_KEY%"

REM ---- draft layer: OpenCode GO subscription ----
set "OPENCODE_API_KEY=%OC_KEY%"

set "PYTHONPATH=%~dp0"
cd /d "%~dp0"

start "jev-chat-windows" ".venv\Scripts\pythonw.exe" main.py

endlocal & exit /b 0
