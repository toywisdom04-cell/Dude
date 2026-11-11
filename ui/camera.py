"""Camera mirror over DUDE's shared capture source."""
import logging
import threading
import time

logger = logging.getLogger(__name__)


class MirrorWindow:
    def __init__(self, camera_index: int = 0):
        self.camera_index = camera_index
        self._thread = None
        self._running = False
        self._stop = threading.Event()
        self._camera = None

    def open(self) -> bool:
        if self._running:
            return True
        from core.orchestrator.hand_tracking import get_hand_camera
        self._camera = get_hand_camera(self.camera_index)
        if not self._camera.start():
            logger.warning("No shared camera available at index %s: %s",
                           self.camera_index, self._camera.last_error)
            self._camera = None
            return False
        self._running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            import cv2
            last_id = -1
            while self._running and not self._stop.is_set():
                frame, frame_id = self._camera.latest()
                if frame is not None and frame_id != last_id:
                    last_id = frame_id
                    cv2.imshow("DUDE - Mirror", frame)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q")):
                        break
                else:
                    time.sleep(0.03)
            cv2.destroyAllWindows()
        except Exception as exc:
            logger.warning("Mirror window error: %s", exc)
        finally:
            self._running = False
            if self._camera is not None:
                self._camera.stop()
            self._camera = None

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        self._running = False


_mirror = None


def open_mirror() -> bool:
    global _mirror
    if _mirror is not None:
        _mirror.close()
    from core.config import get_config
    index = int(get_config().get("hand", "camera_index", default=0))
    _mirror = MirrorWindow(index)
    return _mirror.open()


def close_mirror() -> None:
    global _mirror
    if _mirror is not None:
        _mirror.close()
        _mirror = None

