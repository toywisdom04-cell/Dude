"""Phase 15 generic spatial-workspace tests.

No applications, no UI, no network, no model calls, no real overlay:
snapshots are hand-built fakes and the overlay runs on the headless
memory backend. Geometry is synthetic; only the contracts are under
test.
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
from core.orchestrator.visual_state import VisualStateBus
from core.orchestrator.spatial_workspace import (
    SpatialOverlay,
    SpatialTarget,
    SpatialWorkspace,
    resolve_focus,
    resolve_named,
)


def _snap(app="demo-app", title="Demo", controls=(),
          focused=None, ocr=(), bounds=(0, 0, 800, 600)):
    import datetime
    from core.orchestrator.state import Rect
    return PerceptionSnapshot(
        active_app=app,
        active_window={"title": title},
        window_bounds=Rect(*bounds),
        controls=[ControlInfo(name=n, x=x, y=y, w=w, h=h,
                              role=r, enabled=e)
                  for (n, x, y, w, h, r, e) in controls],
        focused_control=(ControlInfo(name=focused[0], x=focused[1],
                                     y=focused[2], w=focused[3],
                                     h=focused[4])
                         if focused else None),
        ocr_text=" ".join(t for t, _ in ocr),
        ocr_regions=[OCRRegion(text=t, x=r[0], y=r[1], w=r[2], h=r[3])
                     for t, r in ocr],
        captured_at=datetime.datetime.now(),
        capture_method=PerceptionLevel.LEVEL_2_UIA_TREE,
        age_seconds=0.0,
    )


def _bus_with(content="uiae"):
    bus = VisualStateBus()
    if content == "uiae":
        bus.publish(_snap(
            controls=[("Save", 100, 200, 80, 30, "Button", True),
                      ("Cancel", 200, 200, 80, 30, "Button", True)],
            focused=("Save", 100, 200, 80, 30),
            ocr=[("hello", (10, 10, 50, 20))]), source="t")
    elif content == "ocr-only":
        bus.publish(_snap(ocr=[(" totals row ", (300, 400, 90, 22))]),
                    source="t")
    return bus


def _ws(bus):
    return SpatialWorkspace(bus=bus,
                            overlay=SpatialOverlay(backend="memory"))


class Phase15SpatialTest(unittest.TestCase):
    def test_a_state_to_spatial_conversion(self):
        bus = _bus_with()
        st = bus.get()
        self.assertEqual(st.control_rects,
                         ((100, 200, 80, 30), (200, 200, 80, 30)))
        self.assertEqual(st.control_roles, ("Button", "Button"))
        self.assertEqual(st.control_enabled, (True, True))
        self.assertEqual(st.ocr_items, (("hello", (10, 10, 50, 20)),))

    def test_b_uia_rectangle_preservation(self):
        bus = _bus_with()
        t = resolve_named("Save", bus.get())
        self.assertIsNotNone(t)
        self.assertEqual(t.kind, "uia")
        self.assertEqual(t.rect, (100, 200, 80, 30))
        self.assertEqual(t.window, ("demo-app", "Demo"))
        self.assertTrue(t.provenance.startswith("uia:"))

    def test_c_ocr_region_preservation(self):
        bus = _bus_with("ocr-only")
        t = resolve_named("totals row", bus.get())
        self.assertIsNotNone(t)
        self.assertEqual(t.kind, "ocr")
        self.assertEqual(t.rect, (300, 400, 90, 22))
        self.assertTrue(t.provenance.startswith("ocr:"))
        # Unknown names never produce a guessed box.
        self.assertIsNone(resolve_named("no such thing", bus.get()))

    def test_d_reference_freshness(self):
        bus = _bus_with()
        ws = _ws(bus)
        t = ws.present(expression="Save")
        self.assertIsNotNone(t)
        self.assertTrue(t.is_live(bus))
        self.assertEqual(ws.current(), [t])

    def test_e_stale_target_rejected(self):
        bus = VisualStateBus(stale_after_seconds=0.05)
        bus.publish(_snap(controls=[
            ("Save", 100, 200, 80, 30, "Button", True)]), source="t")
        ws = SpatialWorkspace(
            bus=bus, overlay=SpatialOverlay(backend="memory"),
            ttl_seconds=0.05)
        t = ws.present(expression="Save")
        self.assertIsNotNone(t)
        time.sleep(0.08)
        # Neither the target nor the workspace honours stale geometry.
        self.assertFalse(t.is_live(bus, 0.05))
        self.assertEqual(ws.current(), [])
        self.assertIsNone(ws.present(expression="Save"))

    def test_f_geometry_update_on_move(self):
        bus = VisualStateBus()
        bus.publish(_snap(
            controls=[("Save", 100, 200, 80, 30, "Button", True)]),
            source="t1")
        ws = _ws(bus)
        first = ws.present(expression="Save")
        self.assertEqual(first.rect, (100, 200, 80, 30))
        # Window moved: the workspace follows the newly observed
        # geometry instead of reusing the old coordinates.
        bus.publish(_snap(
            controls=[("Save", 500, 600, 80, 30, "Button", True)]),
            source="t2")
        followed = ws.current()
        self.assertEqual(len(followed), 1)
        self.assertEqual(followed[0].rect, (500, 600, 80, 30))
        self.assertNotEqual(followed[0].rect, first.rect)
        # Element vanished: nothing is shown, no blind reuse.
        bus.publish(_snap(controls=[
            ("Cancel", 10, 10, 40, 20, "Button", True)]), source="t3")
        self.assertEqual(ws.current(), [])

    def test_g_multiple_targets(self):
        bus = _bus_with()
        ws = _ws(bus)
        shown = ws.show_many([resolve_named("Save", bus.get()),
                              resolve_named("Cancel", bus.get()),
                              resolve_named("ghost", bus.get())])
        self.assertEqual(len(shown), 2)
        self.assertEqual({t.name for t in shown}, {"Save", "Cancel"})
        self.assertEqual(len(ws.overlay._impl.shown[-1]), 2)

    def test_h_overlay_subscribe_update_clear(self):
        bus = _bus_with()
        ws = _ws(bus)
        ws.present(expression="Save")
        self.assertEqual(len(ws.current()), 1)
        # Environment change auto-clears via the bus subscription.
        bus.publish(_snap(app="other-app", title="Other"), source="t2")
        self.assertEqual(ws.current(), [])
        self.assertGreaterEqual(ws.overlay._impl.clears, 1)
        ws.clear()
        self.assertEqual(ws.current(), [])

    def test_i_overlay_failure_isolated(self):
        bus = _bus_with()

        class _Dead:
            is_up = False
            last_error = "boom"

            def show(self, targets):
                raise RuntimeError("boom")

            def clear(self):
                raise RuntimeError("boom")

            def shutdown(self):
                pass

        ws = SpatialWorkspace(bus=bus, overlay=_Dead())
        self.assertIsNone(ws.present(expression="Save"))
        # Perception and bus are untouched by the overlay failure.
        self.assertIsNotNone(bus.get())
        self.assertEqual(bus.get().active_app, "demo-app")
        self.assertEqual(ws.status()["state"], "DEGRADED")

    def test_j_presentation_does_not_mutate_bus(self):
        bus = _bus_with()
        before = bus.get().to_dict()
        ws = _ws(bus)
        ws.present(expression="Save")
        ws.present(focus=True)
        ws.clear()
        after = bus.get().to_dict()
        before.pop("age_seconds", None)
        after.pop("age_seconds", None)
        self.assertEqual(before, after)

    def test_k_no_screenshot_accumulation(self):
        bus = VisualStateBus()
        ws = _ws(bus)
        big = b"\xff" * (256 * 1024)
        for i in range(20):
            s = _snap(controls=[
                (f"Btn{i}", i, i, 40, 20, "Button", True)])
            s.screenshot = big
            bus.publish(s, source="t")
            ws.present(expression=f"Btn{i}")
        cur = bus.get()
        for value in vars(cur).values():
            self.assertNotIsInstance(value, (bytes, bytearray))
        for t in ws.current():
            for value in vars(t).values():
                self.assertNotIsInstance(value, (bytes, bytearray))

    def test_l_capability_reporting(self):
        ws = _ws(_bus_with())
        self.assertEqual(ws.status()["state"], "AVAILABLE")
        self.assertEqual(_ws(VisualStateBus()).status()["state"],
                         "DEGRADED")  # overlay up, no fresh state
        self.assertEqual(
            SpatialWorkspace(
                bus=None,
                overlay=SpatialOverlay(backend="memory")).status()["state"],
            "UNAVAILABLE")
        # Unresolvable requests degrade gracefully, never raise.
        ws2 = _ws(_bus_with())
        self.assertIsNone(ws2.present(expression="zzz-nope"))
        self.assertTrue(ws2.last_error)


if __name__ == "__main__":
    unittest.main()
