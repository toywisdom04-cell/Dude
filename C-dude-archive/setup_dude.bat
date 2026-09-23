@echo off
REM DUDE first-time setup - run ONCE after copying the folder to your PC
cd /d "%~dp0"

echo Installing DUDE dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyaudio pyttsx3

echo.
echo Done. Now run start_dude.bat (or the first-run wizard with: python -m dude --wizard)
pause
