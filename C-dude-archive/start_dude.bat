@echo off
REM DUDE launcher - run from the folder where this file lives
cd /d "%~dp0"

REM Optional: enable a virtual environment if you made one
REM call venv\Scripts\activate.bat

python -m dude
if errorlevel 1 (
  echo.
  echo DUDE failed to start. Make sure Python is installed and on PATH.
  echo Run: pip install -r requirements.txt
  pause
)
