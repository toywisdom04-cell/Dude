@echo off
rem DuDE hidden launcher - shows no console, keeps voice, writes logs
cd /d "E:\Dude\dude"
"C:\Users\duvvu\AppData\Local\Programs\Python\Python313\python.exe" -u "E:\Dude\dude\dude.py" >> "E:\Dude\dude\data\logs\dude_auto.log" 2>> "E:\Dude\dude\data\logs\dude_auto_err.log"