import threading
import time

from core.config import get_config
from core.personality import active_window


class Tracker:
    def __init__(self, memory):
        self.memory = memory
        cfg = get_config()
        self._poll = int(cfg.get("tracker", "poll_seconds", default=10))
        enabled = cfg.get("tracker", "enabled", default=True)
        if not enabled:
            self._thread = None
            return
        self._running = True
        self.current = active_window()
        self.memory.open_work_session(self.current["app"], self.current["title"])
        self._thread = threading.Thread(target=self._loop, daemon=True, name="du-tracker")
        self._thread.start()

    def _loop(self):
        while self._running:
            time.sleep(self._poll)
            try:
                w = active_window()
                if (w["app"], w["title"]) != (self.current["app"], self.current["title"]):
                    self.current = w
                    self.memory.open_work_session(w["app"], w["title"])
            except Exception as e:
                print(f"[tracker] {e}")

    def stop(self):
        if not getattr(self, "_running", False):
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        try:
            self.memory.close_open_sessions()
        except Exception:
            pass
