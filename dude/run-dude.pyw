import os
import subprocess
import sys
import time

if sys.stdout is None or sys.stderr is None:
    try:
        os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "logs"), exist_ok=True)
        _f = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "logs", "watch.log"),
                  "a", encoding="utf-8", errors="replace")
        if sys.stdout is None:
            sys.stdout = _f
        if sys.stderr is None:
            sys.stderr = _f
    except Exception:
        pass

base = os.path.dirname(os.path.abspath(__file__))
pythonw = sys.executable.replace("python.exe", "pythonw.exe")
if not os.path.exists(pythonw):
    pythonw = sys.executable
script = os.path.join(base, "dude.py")

while True:
    p = subprocess.Popen([pythonw, script], creationflags=0x08000000, cwd=base)
    code = p.wait()
    if code == 2:  # another DUDE instance holds the lock
        break
    if code == 0:  # clean shutdown ("turn off dude")
        break
    time.sleep(3)