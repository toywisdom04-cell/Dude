"""Phase 16 generic hand/gesture tests.

No camera, no model, no UI, no network. Landmarks are hand-built
synthetic geometry; the bus/tracker/camera are constructed directly
(never process singletons). Nothing here names an application.
"""
import threading
import time
import unittest

from core.orchestrator.hand_tracking import (
    Gesture,
    HandObservation,
    HandState,
    HandStateBus,
    HandStabilizer,
    HandCamera,
    HandPerception,
    HandScreenMapper,
    HandTracker,
    classify_gesture,
    relate_to_visual,
)
from core.orchestrator.visual_state import VisualState, VisualStateBus


def _base():
    # Folded fist: tips BELOW pips (closer to the wrist at y=0.8).
    pts = [(0.5, 0.8)]  # wrist
    for x in (0.35, 0.45, 0.55, 0.62):
        pts += [(x, 0.62), (x, 0.58), (x, 0.64), (x, 0.68)]
    pts += [(0.38, 0.66), (0.33, 0.62), (0.30, 0.60), (0.28, 0.58)]
    order = [pts[0]]
    # MediaPipe order: wrist, thumb(1-4), index(5-8), middle(9-12),
    # ring(13-16), pinky(17-20). _base built wrist, 4 fingers, thumb.
    return [pts[0], pts[17], pts[18], pts[19], pts[20],
            pts[1], pts[2], pts[3], pts[4],
            pts[5], pts[6], pts[7], pts[8],
            pts[9], pts[10], pts[11], pts[12],
            pts[13], pts[14], pts[15], pts[16]]


def _open():
    lm = _base()
    xs = {5: 0.40, 9: 0.48, 13: 0.56, 17: 0.63}
    for mcp, x in xs.items():
        lm[mcp] = (x, 0.60)
        lm[mcp + 1] = (x, 0.45)
        lm[mcp + 2] = (x, 0.32)
        lm[mcp + 3] = (x, 0.20)
    lm[1], lm[2], lm[3], lm[4] = ((0.36, 0.66), (0.28, 0.60),
                                  (0.22, 0.55), (0.16, 0.50))
    return lm


def _fist():
    return _base()


def _pointing():
    lm = _base()
    lm[5], lm[6], lm[7], lm[8] = ((0.40, 0.60), (0.40, 0.45),
                                  (0.40, 0.32), (0.40, 0.20))
    return lm


def _pinch():
    lm = _pointing()
    lm[4] = (0.41, 0.22)  # thumb tip onto index tip
    return lm


def _thumbsup():
    lm = _base()
    lm[1], lm[2], lm[3], lm[4] = ((0.44, 0.62), (0.46, 0.50),
                                  (0.47, 0.38), (0.48, 0.26))
    return lm


def _vbus():
    from core.orchestrator.state import ControlInfo, Rect
    import datetime
    from core.orchestrator.state import PerceptionSnapshot, PerceptionLevel
    bus = VisualStateBus()
    snap = PerceptionSnapshot(
        active_app="demo", active_window={"title": "Demo"},
        window_bounds=Rect(0, 0, 1000, 800),
        controls=[ControlInfo(name="Save", x=100, y=100, w=200, h=60)],
        captured_at=datetime.datetime.now(),
        capture_method=PerceptionLevel.LEVEL_2_UIA_TREE, age_seconds=0.0)
    bus.publish(snap, source="t")
    return bus


class Phase16HandTest(unittest.TestCase):
    def test_camera_pipeline_keeps_at_most_two_hands(self):
        perception = HandPerception()
        state = perception.state_from_landmarks([
            (_open(), "Left", 0.9),
            (_open(), "Right", 0.9),
            (_open(), "Center", 0.9),
        ])
        self.assertEqual(state.count(), 2)
        self.assertEqual(set(state.identities()), {"hand_0", "hand_1"})

    def test_a_schema(self):
        p = HandPerception()
        st = p.state_from_landmarks([(_open(), "Right", 0.9)])
        self.assertEqual(st.count(), 1)
        h = st.primary()
        self.assertEqual(h.label, "right")
        self.assertEqual(len(h.landmarks), 21)
        self.assertEqual(set(h.fingertips), {
            "thumb", "index", "middle", "ring", "pinky"})
        self.assertEqual(h.gesture, Gesture.OPEN)
        self.assertGreaterEqual(h.gesture_confidence, 0.6)
        for f in ("wrist", "palm", "bbox"):
            self.assertTrue(getattr(h, f))
        for key in ("hands", "captured_at", "source", "frame_id"):
            self.assertTrue(hasattr(st, key), key)

    def test_b_replacement(self):
        bus = HandStateBus()
        p = HandPerception()
        bus.publish(p.state_from_landmarks([(_open(), "R", 0.9)]))
        bus.publish(p.state_from_landmarks([(_fist(), "R", 0.9)]))
        self.assertEqual(bus.get().primary().gesture, Gesture.CLOSED)
        self.assertEqual(bus.publish_count, 2)
        self.assertFalse(hasattr(bus, "_frames"))
        self.assertFalse(hasattr(bus, "_history"))

    def test_c_freshness_expiration(self):
        bus = HandStateBus(ttl_seconds=0.05)
        p = HandPerception()
        bus.publish(p.state_from_landmarks([(_open(), "R", 0.9)]))
        self.assertIsNotNone(bus.get_fresh())
        time.sleep(0.08)
        self.assertIsNone(bus.get_fresh())
        self.assertTrue(bus.get().is_stale(0.05))

    def test_d_confidence_handling(self):
        g, c = classify_gesture(_open())
        self.assertEqual(g, Gesture.OPEN)
        self.assertGreaterEqual(c, 0.6)
        g, c = classify_gesture(_fist())
        self.assertEqual(g, Gesture.CLOSED)
        g, c = classify_gesture(_pointing())
        self.assertEqual(g, Gesture.POINTING)
        g, c = classify_gesture(_pinch())
        self.assertEqual(g, Gesture.PINCH)
        three_finger = _base()
        for mcp, x in ((5, 0.40), (9, 0.48), (13, 0.56)):
            three_finger[mcp] = (x, 0.60)
            three_finger[mcp + 1] = (x, 0.45)
            three_finger[mcp + 2] = (x, 0.32)
            three_finger[mcp + 3] = (x, 0.20)
        three_finger[17], three_finger[18], three_finger[19], three_finger[20] = (
            (0.63, 0.60), (0.64, 0.58), (0.65, 0.64), (0.66, 0.68))
        g, c = classify_gesture(three_finger)
        self.assertEqual(g, Gesture.THREE_FINGER)
        near_pinch = _pinch()
        near_pinch[4] = (0.49, 0.20)
        g, c = classify_gesture(near_pinch)
        self.assertNotEqual(g, Gesture.PINCH)
        g, c = classify_gesture(_thumbsup())
        self.assertEqual(g, Gesture.THUMBS_UP)
        raised = _open()
        # Raised = open hand riding high: wrist up, fingers above it.
        raised[0] = (0.5, 0.35)
        for mcp in (5, 9, 13, 17):
            x = raised[mcp][0]
            raised[mcp] = (x, 0.28)
            raised[mcp + 1] = (x, 0.18)
            raised[mcp + 2] = (x, 0.12)
            raised[mcp + 3] = (x, 0.06)
        raised[1], raised[2], raised[3], raised[4] = (
            (0.40, 0.30), (0.32, 0.24), (0.26, 0.18), (0.20, 0.12))
        g, c = classify_gesture(raised)
        self.assertEqual(g, Gesture.RAISED)
        g, c = classify_gesture([])
        self.assertEqual(g, Gesture.UNKNOWN)
        self.assertEqual(c, 0.0)

    def test_e_temporal_stabilization(self):
        s = HandStabilizer(votes=5, need=3)
        self.assertEqual(s.update(Gesture.POINTING)[0], Gesture.UNKNOWN)
        self.assertEqual(s.update(Gesture.POINTING)[0], Gesture.UNKNOWN)
        g, votes = s.update(Gesture.POINTING)
        self.assertEqual(g, Gesture.POINTING)
        self.assertEqual(votes, 3)
        # Flicker does not hijack the stable read.
        s.update(Gesture.OPEN)
        g, _ = s.update(Gesture.POINTING)
        self.assertEqual(g, Gesture.POINTING)
        # Bounded: old votes age out of the window.
        for _ in range(5):
            s.update(Gesture.OPEN)
        self.assertEqual(s.update(Gesture.OPEN)[0], Gesture.OPEN)
        s.reset()
        self.assertEqual(s.update(Gesture.OPEN)[0], Gesture.UNKNOWN)

    def test_f_normalization(self):
        p = HandPerception()
        st = p.state_from_landmarks([(_open(), "R", 0.9)])
        h = st.primary()
        for pt in list(h.landmarks) + [h.wrist, h.palm]:
            self.assertTrue(0.0 <= pt[0] <= 1.0)
            self.assertTrue(0.0 <= pt[1] <= 1.0)
        x, y, w, hh = h.bbox
        self.assertTrue(0.0 <= x <= 1.0 and w > 0 and hh > 0)

    def test_g_mapping(self):
        m = HandScreenMapper(mirror=False)
        m.display_rect = lambda: (0, 0, 1000, 500)
        self.assertEqual(m.map(0.0, 0.0), (15.0, 7.5))
        self.assertEqual(m.map(1.0, 1.0), (985.0, 492.5))
        self.assertEqual(m.map(0.25, 0.5), (257.5, 250.0))

    def test_h_mirroring(self):
        m = HandScreenMapper(mirror=True)
        m.display_rect = lambda: (0, 0, 1000, 500)
        plain = HandScreenMapper(mirror=False)
        plain.display_rect = lambda: (0, 0, 1000, 500)
        self.assertEqual(m.map(0.0, 0.5)[0], 985.0)
        self.assertEqual(plain.map(0.0, 0.5)[0], 15.0)
        # Mirror only flips x, never y.
        self.assertEqual(m.map(0.3, 0.2)[1], plain.map(0.3, 0.2)[1])

    def test_i_calibration(self):
        m = HandScreenMapper(mirror=False)
        m.display_rect = lambda: (10, 20, 1000, 500)
        ok = m.calibrate([(0, 0, 10, 20), (1, 0, 1010, 20),
                          (0, 1, 10, 520), (1, 1, 1010, 520)])
        self.assertTrue(ok)
        self.assertTrue(m.calibrated)
        x, y = m.map(0.5, 0.5)
        self.assertAlmostEqual(x, 510, delta=2)
        self.assertAlmostEqual(y, 270, delta=2)
        self.assertTrue(m.calibration_live())
        self.assertFalse(m.calibrate([(0, 0, 0, 0)]))

    def test_j_stale_visual_rejection(self):
        """Stale visual evidence must never ground a click.

        A stale bus is still rejected outright. What is allowed - and required
        for OS-level hand actions to work independently of any DUDE panel - is
        a freshly resolved window under the fingertip. So the assertion is
        that nothing derived from the stale state is ever used.
        """
        bus = _vbus()
        bus.stale_after_seconds = 0.05
        time.sleep(0.08)
        rel = relate_to_visual((150, 130), bus)
        if rel is not None:
            # Permitted only as a live window-under-point resolution.
            self.assertEqual(rel.get("provenance"),
                             "hand+point:window_under_point")
            self.assertEqual(rel.get("kind"), "window")
        else:
            self.assertIsNone(rel)

    def test_k_failure_isolation(self):
        # NOTE: real opens on dead indices can wedge DSHOW teardown, so
        # failure paths are proven with a stubbed cv2 (our isolation
        # logic is what's under test, not the driver).
        import sys
        from unittest import mock

        class _DeadCap:
            def __init__(self, *a, **k):
                raise RuntimeError("no such device")

        _stub = mock.Mock()
        _stub.VideoCapture = _DeadCap
        _stub.CAP_DSHOW = 700
        with mock.patch.dict(sys.modules, {"cv2": _stub}):
            cam = HandCamera(index=9999)
            self.assertFalse(cam.start())
            self.assertTrue(cam.last_error)
            tr = HandTracker()
            self.assertFalse(tr.start_camera(index=9999))
        p = HandPerception(model_path="Z:\\nope\\missing.task")
        self.assertFalse(p.load())
        self.assertIsNone(p.process_frame(None))
        # Camera failure degrades the capability, never the callers.
        self.assertEqual(tr.status()["state"], "UNAVAILABLE")
        self.assertIsNone(tr.tick())
        self.assertIsNone(tr.pointed_element(_vbus()))
        self.assertFalse(tr.camera_on)
        # A wedged driver open must time out, never hang the caller.
        _wedged = mock.Mock()
        _wedged.CAP_DSHOW = 700

        def _block(*a, **k):
            threading.Event().wait(60)
            raise RuntimeError("unreachable")

        _wedged.VideoCapture = _block
        with mock.patch.dict(sys.modules, {"cv2": _wedged}):
            cam2 = HandCamera(index=9999)
            t0 = time.time()
            self.assertFalse(cam2.start())
            self.assertLess(time.time() - t0, 30)
            self.assertIn("timed out", cam2.last_error)
        # Release the really-loaded model: native threads wedge exit.
        tr.shutdown()

    def test_l_self_model(self):
        tr = HandTracker()
        self.assertEqual(tr.status()["state"], "UNAVAILABLE")
        tr2 = HandTracker.__new__(HandTracker)
        tr2.camera = HandCamera(index=9999)
        tr2.perception = HandPerception(model_path="Z:\\nope.task")
        tr2.bus = HandStateBus()
        tr2.camera._running = True  # simulate streaming, model missing
        self.assertEqual(tr2.status()["state"], "DEGRADED")

    def test_m_concurrent_readers(self):
        bus = HandStateBus()
        p = HandPerception()
        bus.publish(p.state_from_landmarks([(_open(), "R", 0.9)]))
        errors = []

        def reader():
            try:
                for _ in range(200):
                    s = bus.get()
                    if s is not None:
                        s.primary()
                        s.is_stale()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def writer():
            try:
                for _ in range(30):
                    bus.publish(p.state_from_landmarks(
                        [(_open(), "R", 0.9)]))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        ts = [threading.Thread(target=reader) for _ in range(4)] + \
             [threading.Thread(target=writer) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        self.assertEqual(errors, [])

    def test_n_no_frame_accumulation(self):
        p = HandPerception()
        bus = HandStateBus()
        for _ in range(30):
            bus.publish(p.state_from_landmarks([(_open(), "R", 0.9)]))
        cur = bus.get()
        for value in vars(cur).values():
            self.assertNotIsInstance(value, (bytes, bytearray))
            import numpy as _np
            self.assertNotIsInstance(value, _np.ndarray)
        for h in cur.hands:
            for value in vars(h).values():
                self.assertNotIsInstance(value, (bytes, bytearray))

    def test_o_gesture_preserves_bus(self):
        bus = _vbus()
        before = bus.get().to_dict()
        p = HandPerception()
        hstate = p.state_from_landmarks([(_pointing(), "R", 0.95)])
        rel = relate_to_visual((150, 130), bus)
        self.assertIsNotNone(rel)
        self.assertEqual(rel["kind"], "uia")
        self.assertEqual(rel["element"], "Save")
        # Outside any control but inside the window: coarse region.
        rel2 = relate_to_visual((900, 700), bus)
        self.assertIsNotNone(rel2)
        self.assertEqual(rel2["kind"], "window")
        # Outside everything: honest miss.
        self.assertIsNone(relate_to_visual((5000, 5000), bus))
        after = bus.get().to_dict()
        before.pop("age_seconds", None)
        after.pop("age_seconds", None)
        self.assertEqual(before, after)
        self.assertEqual(hstate.count(), 1)

    def test_tuple_window_bounds_are_grounded(self):
        bus = VisualStateBus(stale_after_seconds=300.0)
        bus.publish(VisualState(
            active_app="Proof", window_title="Proof Window",
            window_bounds=(200, 100, 1200, 700), source="test"),
            source="test")
        rel = relate_to_visual((800, 500), bus)
        self.assertIsNotNone(rel)
        self.assertEqual(rel["kind"], "window")
        self.assertEqual(rel["rect"], (200, 100, 1200, 700))

    def test_p_low_confidence_no_action(self):
        # Low-score pointing: no relation drives anything. The score
        # is the handedness classifier score (the API exposes no
        # detector score); below-bar hands are rejected by the gate.
        bus = _vbus()
        tr = HandTracker(bus=HandStateBus())
        p = HandPerception()
        weak = p.state_from_landmarks([(_pointing(), "R", 0.2)])
        tr.bus.publish(weak)
        tr._stable = (Gesture.POINTING, 3)
        self.assertIsNone(tr.pointed_element(bus))
        # A DETECTED hand (count 1, high gesture confidence) with a
        # below-bar handedness score is still rejected by the gate.
        lowdet = p.state_from_landmarks([(_pointing(), "R", 0.3)])
        self.assertEqual(lowdet.count(), 1)
        self.assertGreaterEqual(
            lowdet.primary().gesture_confidence, 0.6)
        tr.bus.publish(lowdet)
        tr._stable = (Gesture.POINTING, 4)
        self.assertIsNone(tr.pointed_element(bus))
        # Unstable gesture: also nothing.
        strong = p.state_from_landmarks([(_pointing(), "R", 0.95)])
        tr.bus.publish(strong)
        tr._stable = (Gesture.POINTING, 1)
        self.assertIsNone(tr.pointed_element(bus))
        # And structurally: no attribute on the tracker can address
        # the action system.
        for name in ("action", "executor", "click", "actuate", "perform"):
            self.assertFalse(hasattr(tr, name), name)


    def test_q_async_boot_launch(self):
        # Boot path: launch returns immediately, duplicates collapse,
        # hardware/model work stays off the caller.
        import sys
        from unittest import mock
        _wedged = mock.Mock()
        _wedged.CAP_DSHOW = 700

        def _block(*a, **k):
            threading.Event().wait(60)
            raise RuntimeError("unreachable")

        _wedged.VideoCapture = _block
        tr = HandTracker()
        try:
            with mock.patch.dict(sys.modules, {"cv2": _wedged}):
                t0 = time.time()
                self.assertTrue(tr.start_camera_async())
                self.assertFalse(tr.start_camera_async())
                self.assertLess(time.time() - t0, 5)
        finally:
            tr.shutdown()


    def test_r_lens_cover_detector(self):
        from core.orchestrator.hand_tracking import LensCoverDetector
        d = LensCoverDetector(need=10)
        # Normal frames never trip it.
        for _ in range(30):
            self.assertFalse(d.update(90.0, 40.0))
        # Bright-flat (wall) is not a cover.
        for _ in range(30):
            self.assertFalse(d.update(200.0, 5.0))
        # Dark-flat sustained trips it; one live frame resets.
        for _ in range(9):
            self.assertFalse(d.update(8.0, 4.0))
        self.assertTrue(d.update(8.0, 4.0))
        self.assertTrue(d.covered)
        d.update(120.0, 50.0)
        self.assertFalse(d.covered)
        d.reset()
        # Tracker reports cover through status, keeps streaming.
        tr = HandTracker()
        tr.camera._running = True
        tr.perception._landmarker = object()
        tr.bus.publish(HandPerception().state_from_landmarks(
            [(_open(), "R", 0.9)]))
        self.assertEqual(tr.status()["state"], "AVAILABLE")
        tr.camera.lens.covered = True
        st = tr.status()
        self.assertEqual(st["state"], "DEGRADED")
        self.assertIn("cover", st["reason"])
        tr.camera.lens.covered = False
        tr.shutdown()


if __name__ == "__main__":
    unittest.main()
