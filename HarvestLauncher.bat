@echo off
REM Double-click launcher (Windows). Thin wrapper around the cross-platform
REM mediainfo_harvest.py -- runs it with no arguments, which drops it
REM into interactive prompt mode. Any arguments given are passed through.
setlocal
cd /d "%~dp0"

REM Prefer the "py" launcher: on many Windows machines "python" on PATH is the
REM Microsoft Store placeholder, which opens the Store instead of running.
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
    where python >nul 2>nul && set "PYEXE=python"
)

if not defined PYEXE (
    echo ERROR: Python not found.
    echo Install Python 3 from https://www.python.org/downloads/windows/
    echo and make sure to check "Add python.exe to PATH" during installation.
    set "RC=1"
    goto :end
)

%PYEXE% mediainfo_harvest.py %*
set "RC=%errorlevel%"

:end
echo.
pause
exit /b %RC%
