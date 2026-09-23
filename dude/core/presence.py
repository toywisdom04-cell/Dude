import ctypes
import datetime
import threading
import time

from core.config import get_config


class Presence:
    def __init__(self, memory, on_away=None, on_return=None, idle_away_s=None,
                 enabled=None, poll=5):
        self.memory = memory
        self.on_away = on_away
        self.on_return = on_return
        self.away_since = None
        cfg = get_config()
        if enabled is None:
            enabled = bool(cfg.get("presence", "enabled", default=True))
        if idle_away_s is None:
            idle_away_s = int(cfg.get("presence", "idle_away_s", default=180))
        self._poll = int(poll)
        self.idle_away_s = int(idle_away_s)
        self._stop = threading.Event()
        self._thread = None
        if enabled:
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="du-presence")
            self._thread.start()

    def present(self):
        return self.away_since is None

    def _idle_ms(self):
        try:
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(LASTINPUTINFO)
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
            uptime = ctypes.windll.kernel32.GetTickCount()
            return (ctypes.c_uint(uptime - info.dwTime)).value
        except Exception:
            return 0

    def _loop(self):
        while not self._stop.wait(self._poll):
            if self._idle_ms() >= self.idle_away_s * 1000:
                if self.away_since is None:
                    self.away_since = datetime.datetime.now()
                    try:
                        if self.on_away:
                            self.on_away(self.away_since)
                    except Exception:
                        pass
            else:
                if self.away_since is not None:
                    left = self.away_since
                    self.away_since = None
                    try:
                        if self.on_return:
                            self.on_return(left, datetime.datetime.now())
                    except Exception:
                        pass

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)