"""Phase 14 generic Visual State Bus tests.

No applications, no UI, no network, no model calls. Snapshots are
hand-built fakes; the bus is constructed directly (never the process
singleton, so tests stay isolated).
"""
import threading
import time
import unittest

from core.orchestrator.state import (
    ControlInfo,
    OCRRegion,
    PerceptionLevel,
    PerceptionSnapshot,
)
from core.orchestrator.visual_state import (
    VisualStateBus,
    describe_snapshot,
)


def _snap(app="demo-app", title="Demo Window", focused="OK",
          controls=("OK", "Cancel"), ocr="hello world",
          screenshot=b"\x89PNG" + b"x" * 10000):
    return PerceptionSnapshot(
        active_app=app,
        active_window={"title": title},
        controls=[ControlInfo(name=n) for n in controls],
        focused_control=ControlInfo(name=focused) if focused else None,
        ocr_text=ocr,
        ocr_regions=[OCRRegion(text=ocr, confidence=0.9)] if ocr else [],
        screenshot=screenshot,
        screenshot_age=0.1,
        change_detected=False,
        captured_at=__import__("datetime").datetime.now(),
        capture_method=PerceptionLevel.LEVEL_2_UIA_TREE,
        age_seconds=0.0,
    )


class Phase14BusTest(unittest.TestCase):
    def test_publication(self):
        bus = VisualStateBus()
        self.assertIsNone(bus.get())
        st = bus.publish(_snap(), source="engine", goal="do a thing")
        self.assertIsNotNone(st)
        cur = bus.get()
        self.assertEqual(cur.active_app, "demo-app")
        self.assertEqual(cur.window_title, "Demo Window")
        self.assertEqual(cur.focused_name, "OK")
        self.assertEqual(cur.source, "engine")
        self.assertEqual(cur.goal, "do a thing")
        self.assertEqual(cur.capture_method, "LEVEL_2_UIA_TREE")

    def test_replacement_not_accumulation(self):
        bus = VisualStateBus()
        bus.publish(_snap(app="app-a", title="A"))
        bus.publish(_snap(app="app-b", title="B"))
        bus.publish(_snap(app="app-c", title="C"))
        cur = bus.get()
        self.assertEqual(cur.active_app, "app-c")
        self.assertEqual(bus.publish_count, 3)
        # One slot only: no history list anywhere on the bus.
        self.assertFalse(hasattr(bus, "_history"))
        self.assertFalse(hasattr(bus, "_frames"))

    def test_freshness(self):
        bus = VisualStateBus(stale_after_seconds=5.0)
        bus.publish(_snap())
        cur = bus.get()
        self.assertLess(cur.age_seconds(), 5.0)
        self.assertFalse(cur.is_stale(5.0))
        self.assertIsNotNone(bus.get_fresh())
        self.assertIn("app: demo-app", bus.describe())

    def test_stale_expiration(self):
        bus = VisualStateBus(stale_after_seconds=0.05)
        bus.publish(_snap())
        self.assertIsNotNone(bus.get_fresh())
        time.sleep(0.08)
        cur = bus.get()
        self.assertTrue(cur.is_stale(0.05))
        self.assertIsNone(bus.get_fresh())
        # Stale state stays readable but is marked as stale.
        self.assertIn("stale", bus.describe())
        # A fresh publish revives it.
        bus.publish(_snap(app="new-app"))
        self.assertIsNotNone(bus.get_fresh())
        self.assertEqual(bus.get().active_app, "new-app")

    def test_concurrent_readers(self):
        bus = VisualStateBus()
        bus.publish(_snap())
        errors = []

        def reader():
            try:
                for _ in range(200):
                    s = bus.get()
                    if s is not None:
                        s.age_seconds()
                        s.key()
                    bus.describe()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def writer():
            try:
                for i in range(50):
                    bus.publish(_snap(app=f"app-{i}"))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = ([threading.Thread(target=reader) for _ in range(6)]
                   + [threading.Thread(target=writer) for _ in range(2)])
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(bus.publish_count, 1 + 2 * 50)
        self.assertIsNotNone(bus.get())

    def test_subscription(self):
        bus = VisualStateBus()
        seen = []
        unsub = bus.subscribe(seen.append)
        bus.publish(_snap(app="sub-a"))
        bus.publish(_snap(app="sub-b"))
        self.assertEqual([s.active_app for s in seen], ["sub-a", "sub-b"])
        unsub()
        bus.publish(_snap(app="sub-c"))
        self.assertEqual(len(seen), 2)
        # A failing subscriber never breaks publication.
        bus.subscribe(lambda s: 1 / 0)
        st = bus.publish(_snap(app="sub-d"))
        self.assertEqual(st.active_app, "sub-d")

    def test_provenance(self):
        bus = VisualStateBus()
        bus.publish(_snap(), source="task_engine:_do_acting",
                    goal="my goal")
        cur = bus.get()
        self.assertEqual(cur.source, "task_engine:_do_acting")
        self.assertEqual(cur.goal, "my goal")
        self.assertEqual(cur.capture_method, "LEVEL_2_UIA_TREE")
        self.assertGreater(cur.published_at, 0)
        self.assertGreaterEqual(cur.published_at, cur.captured_at - 1.0)

    def test_consistency_with_perception(self):
        bus = VisualStateBus()
        snap = _snap(app="cons-app", title="Cons", focused="Save",
                     controls=("Save", "Name:", "Cancel"),
                     ocr="file name here")
        cur = bus.publish(snap, source="voice_controller")
        self.assertEqual(cur.active_app, snap.active_app)
        self.assertEqual(cur.window_title, "Cons")
        self.assertEqual(cur.focused_name, "Save")
        self.assertEqual(cur.control_count, 3)
        self.assertEqual(tuple(cur.control_names), ("Save", "Name:", "Cancel"))
        self.assertIn("file name here", cur.ocr_excerpt)
        text = describe_snapshot(snap)
        for token in ("cons-app", "Cons", "Save", "file name here"):
            self.assertIn(token, text)
        # Bus words match the direct rendering of the same snapshot.
        self.assertEqual(bus.describe().split("\n")[:5],
                         describe_snapshot(snap).split("\n")[:5])

    def test_no_screenshot_accumulation(self):
        bus = VisualStateBus()
        big = b"\xff" * (256 * 1024)
        for i in range(30):
            bus.publish(_snap(app=f"shot-{i}", screenshot=big))
        cur = bus.get()
        self.assertEqual(cur.active_app, "shot-29")
        self.assertTrue(cur.screenshot_present)
        # No pixel bytes anywhere on the state or the bus.
        for value in vars(cur).values():
            self.assertNotIsInstance(value, (bytes, bytearray))
        for name in ("screenshot", "frame", "frames", "image", "pixels"):
            self.assertFalse(hasattr(cur, name),
                             f"state must not carry {name}")
            self.assertFalse(hasattr(bus, name),
                             f"bus must not carry {name}")


if __name__ == "__main__":
    unittest.main()
