' Desktop launcher for DUDE - same silent, connected chain as the autostart entry:
' Desktop shortcut -> this VBS -> dude_run.bat -> python -u dude.py
' No console window, voice on, logs to dude_auto.log. The single-instance lock
' inside dude.py guarantees double-clicking never spawns a second agent.
Set ws = CreateObject("WScript.Shell")
ws.Run "cmd /c ""E:\Dude\dude\autostart\dude_run.bat""", 0, False