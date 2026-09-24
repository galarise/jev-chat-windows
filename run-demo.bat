@echo off
REM ---------------------------------------------------------------
REM  jev-chat-windows - run the end-to-end demo with keys loaded
REM  from the local key folder. Keys are never written into files
REM  in this repo; they are only exported into this cmd session.
REM
REM  Usage:  run-demo.bat                 (both samples)
REM          run-demo.bat --sample work   (work sample only)
REM          run-demo.bat --sample romance
REM ---------------------------------------------------------------
setlocal

REM Override KEYDIR before running if your key folder is elsewhere.
if not defined KEYDIR set "KEYDIR=E:\workdata\key"
if not exist "%KEYDIR%\jev.key"      ( echo [ERR] missing %KEYDIR%\jev.key & exit /b 1 )
if not exist "%KEYDIR%\opencode.key" ( echo [ERR] missing %KEYDIR%\opencode.key & exit /b 1 )

set "JEVC_KEY="
set "OC_KEY="
set /p JEVC_KEY=<"%KEYDIR%\jev.key"
set /p OC_KEY=<"%KEYDIR%\opencode.key"

REM judge layer: third-party hosted proxy in front of the official Jev API
set "JEVC_JUDGE_URL=https://jevtypesafeai.com/api/v1/decide"
set "JEVC_JUDGE_MODEL=jev-latest"
set "OPENROUTER_API_KEY=%JEVC_KEY%"

REM draft layer: opencode-go (deepseek-v4.1-flash)
set "OPENCODE_API_KEY=%OC_KEY%"

set "PYTHONPATH=%~dp0"
cd /d "%~dp0"

python tools\demo.py %*
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
