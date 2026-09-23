"""Screen observation for the learning module.

Captures periodic screenshots (opt-in) so the learning pass can derive
"which app / which file / which task" context. Everything stays local.
"""
import datetime
from pathlib import Path


class ScreenObserver:
    def __init__(self, data_dir: str = "./dude_data", enabled: bool = False,
                 interval_seconds: int = 300):
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self.dir = Path(data_dir) / "screen_observations"
        self.dir.mkdir(parents=True, exist_ok=True)

    def capture(self) -> str | None:
        """Take one screenshot; returns path or None."""
        if not self.enabled:
            return None
        try:
            import pyautogui
            name = f"obs_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
            path = str(self.dir / name)
            pyautogui.screenshot().save(path)
            return path
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Screen capture failed: %s", exc)
            return None

    def should_capture(self, last_capture_at: datetime.datetime | None) -> bool:
        if not self.enabled:
            return False
        if last_capture_at is None:
            return True
        return (datetime.datetime.now() - last_capture_at).total_seconds() >= self.interval_seconds

