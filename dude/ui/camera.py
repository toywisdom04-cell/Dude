"""Camera / mirror window using OpenCV.

Opens a local mirror window (like the original DUDE's camera feature).
The window closes when 'q' is pressed or close_mirror() is called.
Works on all platforms where OpenCV can access a camera.
"""
import logging
import threading

logger = logging.getLogger(__name__)


class MirrorWindow:
    def __init__(self, camera_index: int = 0):
        self.camera_index = camera_index
        self._thread = None
        self._running = False

    def open(self) -> bool:
        if self._running:
            return True
        try:
            import cv2
            cam = cv2.VideoCapture(self.camera_index)
            ok, _ = cam.read()
            cam.release()
            if not ok:
                logger.warning("No camera available at index %s", self.camera_index)
                return False
        except Exception as exc:
            logger.warning("Camera open failed: %s", exc)
            return False
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            import cv2
            cam = cv2.VideoCapture(self.camera_index)
            while self._running:
                ret, frame = cam.read()
                if not ret:
                    break
                cv2.imshow("DUDE - Mirror", frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q")):
                    break
            cam.release()
            cv2.destroyAllWindows()
        except Exception as exc:
            logger.warning("Mirror window error: %s", exc)
        finally:
            self._running = False

    def close(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


_mirror = None


def open_mirror() -> bool:
    global _mirror
    _mirror = MirrorWindow()
    return _mirror.open()


def close_mirror() -> None:
    global _mirror
    if _mirror is not None:
        _mirror.close()
        _mirror = None

