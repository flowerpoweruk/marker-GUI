@echo off
REM Double-click this to start Marker Studio.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 bootstrap.py
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python bootstrap.py
    goto :end
)

echo.
echo   Python 3 isn't installed.
echo   Get it from https://www.python.org/downloads/
echo   During install, tick "Add Python to PATH", then run this again.
echo.
pause

:end
