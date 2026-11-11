"""Phase 16 Command Centre panel tests (headless/offscreen Qt).

Fake tracker/visual bus; asserts the panel is display-only, hidden
unless the camera runs, and immune to dead sources. No camera, no
model, no network.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
except Exception:
    _APP = None

from core.holo_hand import HoloHandPanel
from core.hand_pointer_overlay import HandScreenPointerOverlay


class _FakeCam:
    def __init__(self, on=True, frame=None, index=0):
        self.camera_on_flag = on
        self._frame = frame
        self.index = index


class _FakeTracker:
    def __init__(self, on=True, state=None, stable=("unknown", 0),
                 status=None, tip=None, target=None):
        self.camera_on = on
        self.camera = _FakeCam(on)
        self._state = state
        self._stable = stable
        self._status = status or {"state": "AVAILABLE"}
        self._tip = tip
        self._target = target
        import types
        self.perception = types.SimpleNamespace(available=True)
        self.mapper = types.SimpleNamespace(
            calibrated=False, calibration_live=lambda: False,
            map=lambda x, y: (500, 300),
            display_rect=lambda: (0, 0, 1000, 600))

        class _Bus:
            def __init__(self, st):
                self._st = st

            def get(self):
                return self._st

        self.bus = _Bus(state)

    def stable(self):
        return self._stable

    def status(self):
        return dict(self._status)

    def pointed_element(self, _vb):
        return self._target


def _hand_state():
    from core.orchestrator.hand_tracking import (
        HandObservation, HandState, Gesture)
    import time
    lm = tuple((0.1 + 0.04 * (i % 7), 0.1 + 0.04 * (i // 7))
               for i in range(21))
    h = HandObservation(label="right", wrist=(0.5, 0.8),
                        palm=(0.5, 0.55), bbox=(0.2, 0.2, 0.5, 0.5),
                        landmarks=lm, handedness_score=0.9,
                        gesture=Gesture.OPEN, gesture_confidence=0.85,
                        captured_at=time.time())
    return HandState(hands=(h,), captured_at=time.time())


class _FakeVBus:
    def get(self):
        import time

        class _S:
            def age_seconds(self):
                return 0.2
        return _S()


class PanelTest(unittest.TestCase):
    def test_hidden_unless_camera_runs(self):
        p = HoloHandPanel()
        self.assertFalse(p.visible())
        p.attach(_FakeTracker(on=False), _FakeVBus())
        p.refresh()
        self.assertFalse(p.visible())
        self.assertIsNone(p._img)

    def test_refresh_and_paint_with_hand(self):
        import numpy as np
        frame = (np.arange(480 * 640 * 3, dtype=np.uint8)
                 .reshape(480, 640, 3))
        tr = _FakeTracker(on=True, state=_hand_state(),
                          stable=("open", 4),
                          target={"element": "Save", "kind": "uia",
                                  "rect": (10, 10, 50, 20),
                                  "window": ("a", "b"),
                                  "provenance": "hand+uia:Save"})
        tr.camera._frame = frame
        tr.camera.latest = lambda: (frame, 7)
        p = HoloHandPanel()
        p.attach(tr, _FakeVBus())
        self.assertTrue(p.visible())
        p.refresh()
        self.assertIsNotNone(p._img)
        self.assertEqual(p._cache["hands"], 1)
        self.assertIsNotNone(p._cache["target"])
        # Paint onto an offscreen image: must not raise, must draw.
        from PyQt5.QtGui import QImage, QPainter, QColor
        img = QImage(900, 580, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        qp = QPainter(img)
        p.paint(qp, 900)
        qp.end()
        self.assertGreater(p._cache["hands"], 0)

    def test_desktop_overlay_draws_screen_pointer_and_hides_in_spatial_world(self):
        class _PointerTracker:
            def __init__(self):
                self.visible = True
                self.feedback = None
            def spatial_snapshot(self):
                return {"world": "screen" if self.visible else "spatial",
                        "pointers": ([{"hand_id": "hand_0", "label": "right",
                                       "screen_xy": (400, 300),
                                       "tip": (0.4, 0.3)}]
                                      if self.visible else []),
                        "fingertips": [
                            {"hand_id": "hand_0", "finger": name,
                             "screen_xy": (400 + offset, 300 + offset),
                             "active": name == "index"}
                            for offset, name in enumerate(
                                ("thumb", "index", "middle", "ring", "pinky"))
                        ],
                        "hands": ([{"hand_id": "hand_0", "grabbed": True}]
                                  if not self.visible else [])}
            def action_feedback(self):
                return dict(self.feedback or {})
        tracker = _PointerTracker()
        overlay = HandScreenPointerOverlay(tracker)
        overlay.start()
        try:
            overlay.refresh()
            self.assertTrue(overlay.isVisible())
            self.assertEqual(len(overlay._pointers), 1)
            self.assertEqual(overlay._pointers[0]["screen_xy"], (400, 300))
            import time
            tracker.feedback = {
                "success": True, "verified": True,
                "screen_xy": (400, 300), "timestamp": time.time()}
            overlay.refresh()
            self.assertEqual(overlay._feedback["screen_xy"], (400, 300))
            tracker.feedback = None
            tracker.visible = False
            overlay.refresh()
            self.assertFalse(overlay.isVisible())
        finally:
            overlay.stop()

    def test_dead_sources_never_raise(self):
        p = HoloHandPanel()
        p.attach(None, None)
        p.refresh()
        p.attach(_FakeTracker(on=True, state=None), None)
        p.refresh()
        from PyQt5.QtGui import QImage, QPainter, QColor
        img = QImage(900, 580, QImage.Format_ARGB32)
        qp = QPainter(img)
        p.paint(qp, 900)
        qp.end()

    def test_no_frames_retained(self):
        import numpy as np
        tr = _FakeTracker(on=True, state=_hand_state())
        f1 = np.zeros((480, 640, 3), dtype=np.uint8)
        f2 = np.ones((480, 640, 3), dtype=np.uint8) * 255
        tr.camera.latest = lambda: (f1, 1)
        p = HoloHandPanel()
        p.attach(tr, _FakeVBus())
        p.refresh()
        first = p._img
        tr.camera.latest = lambda: (f2, 2)
        p.refresh()
        # Single replaced image only; nothing accumulates.
        self.assertIsNot(p._img, first)
        self.assertFalse(hasattr(p, "_history"))
        self.assertFalse(hasattr(p, "_frames"))


if __name__ == "__main__":
    unittest.main()
