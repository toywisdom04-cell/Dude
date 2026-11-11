@echo off
chcp 65001 >nul
cd /d E:\Dude\dude
python voice.py 2>&1 | Tee-Object -FilePath latency.log
echo Test complete. Check latency.log for results.
pause
