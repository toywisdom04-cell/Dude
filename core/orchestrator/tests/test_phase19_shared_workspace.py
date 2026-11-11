"""Phase 19 generic Multimodal Shared Workspace tests.

Real VisualStateBus, HandStateBus, Memory(temp DB), job_memory,
procedure_store shapes and ContextPrimer. Minimal fakes only for
isolated failure paths. No applications, no model, no network.
"""
import os
import tempfile
import time
import unittest

from core.orchestrator.state import (
    ControlInfo, PerceptionLevel, PerceptionSnapshot, Rect)
from core.orchestrator.visual_state import VisualStateBus
from core.orchestrator.hand_tracking import (
    Gesture, HandObservation, HandState, HandStateBus, HandPerception)
from core.orchestrator.shared_workspace import (
    ModalityEvidence, SharedWorkspace, compose_workspace,
    last_workspace, publish_workspace)
from core.orchestrator.context_primer import ContextBundle, ContextPrimer
from core.capability_bus import CapabilityBus


def _vbus(fresh=True, app="app-a", title="Window A", age=0.0):
    import datetime
    bus = VisualStateBus(stale_after_seconds=5.0)
    snap = PerceptionSnapshot(
        active_app=app, active_window={"title": title},
        window_bounds=Rect(0, 0, 800, 600),
        controls=[ControlInfo(name="Save", x=10, y=20, w=50, h=20)],
        focused_control=ControlInfo(name="Save"),
        captured_at=datetime.datetime.now(),
        capture_method=PerceptionLevel.LEVEL_2_UIA_TREE, age_seconds=0.0)
    bus.publish(snap, source="test")
    if not fresh:
        time.sleep(0.01)
        bus.stale_after_seconds = 0.001
        time.sleep(0.01)
    return bus


def _hbus(present=True, fresh=True, key=("app-a", "Window A")):
    """Hand bus with a REAL 1s hand-state TTL.

    The bus is returned with a `refresh()` hook so tests can place
    genuinely fresh hand evidence at the exact moment they assert on
    it. This removes a machine-speed race in the FIXTURE (evidence
    published, then >1s of work elapses before the assertion) without
    touching production freshness rules or weakening any assertion: the
    test still requires fresh hand evidence AND a valid hand->visual
    correlation before a referent may resolve.
    """
    bus = HandStateBus(ttl_seconds=1.0)
    state = {"value": None}
    if present:
        p = HandPerception()
        from core.orchestrator.tests.test_phase16_hands import _pointing
        state["value"] = p.state_from_landmarks(
            [(_pointing(), "Right", 0.9)])
        bus.publish(state["value"], source="hand_tracker")

    def _refresh():
        # Re-publish a fresh, equivalent hand observation (a real sensor
        # would be publishing these continuously at camera FPS).
        p2 = HandPerception()
        from core.orchestrator.tests.test_phase16_hands import _pointing
        st = p2.state_from_landmarks([(_pointing(), "Right", 0.9)])
        bus.publish(st, source="hand_tracker")
        return bus.get_fresh()

    bus.refresh = _refresh
    if not fresh:
        bus.ttl_seconds = 0.001
        time.sleep(0.01)
    return bus


def _bundle(**kw):
    b = ContextBundle(goal=kw.pop("goal", "do the work"))
    for k, v in kw.items():
        setattr(b, k, v)
    return b


class Phase19WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    # A/B
    def test_a_creation(self):
        ws = compose_workspace(bundle=_bundle())
        self.assertIsInstance(ws, SharedWorkspace)
        self.assertTrue(ws.describe())

    def test_b_bounded(self):
        b = _bundle(memory=[{"fact": "f%d" % i} for i in range(50)],
                    jobs=[{"name": "j%d" % i} for i in range(50)],
                    procedures=[{"goal": "p%d" % i} for i in range(50)],
                    skill_packs=[("s%d" % i, 0.5, "r") for i in range(50)])
        ws = compose_workspace(bundle=b)
        self.assertLessEqual(len(ws.memory), 5)
        self.assertLessEqual(len(ws.jobs), 5)
        self.assertLessEqual(len(ws.procedures), 5)
        self.assertLessEqual(len(ws.skill_packs), 5)
        self.assertLessEqual(len(ws.correlations), 8)

    # C
    def test_c_provenance(self):
        bus = _vbus()
        ws = compose_workspace(bundle=_bundle(), visual_bus=bus)
        self.assertEqual(ws.visual.source, "VisualStateBus")
        self.assertEqual(ws.sources["visual"], "test")
        self.assertIsNotNone(ws.captured_at)
        self.assertEqual(ws.app_window_authority, "VisualStateBus")

    # D/E
    def test_d_visual_attachment(self):
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus())
        self.assertTrue(ws.visual.present)
        self.assertTrue(ws.visual.fresh)
        self.assertEqual(ws.active_app, "app-a")
        self.assertIn("Window A", ws.visual.summary)

    def test_e_hand_attachment(self):
        hb = _hbus()
        hb.refresh()
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                               hand_bus=hb)
        self.assertTrue(ws.hand.present)
        self.assertTrue(ws.hand.fresh)
        self.assertEqual(ws.hand.source, "HandStateBus")

    # F/G/H/I/J
    def test_fghi_attachments(self):
        b = _bundle(memory=[{"fact": "likes tea"}],
                    jobs=[{"name": "reporting", "confidence": 0.7}],
                    procedures=[{"goal": "make report", "confidence": 0.8}],
                    skill_packs=[("sp.doc", 0.7, "overlap")],
                    capabilities=["perception", "memory"])
        ws = compose_workspace(bundle=b)
        self.assertEqual(ws.memory[0]["fact"], "likes tea")
        self.assertEqual(ws.jobs[0]["name"], "reporting")
        self.assertEqual(ws.procedures[0]["goal"], "make report")
        self.assertEqual(ws.skill_packs[0][0], "sp.doc")
        self.assertIn("perception", ws.capabilities)

    # K/L/M
    def test_k_fresh_accepted(self):
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus())
        self.assertEqual(ws.status()["sources"]["visual"], "AVAILABLE")

    def test_l_stale_visual_rejected(self):
        bus = _vbus(fresh=False)
        ws = compose_workspace(bundle=_bundle(), visual_bus=bus)
        self.assertNotEqual(ws.status()["sources"]["visual"], "AVAILABLE")
        self.assertIn("visual_stale", ws.degraded)

    def test_m_stale_hand_rejected(self):
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                               hand_bus=_hbus(fresh=False))
        self.assertIn("hand_stale", ws.degraded)
        self.assertNotEqual(ws.status()["sources"]["hand"], "AVAILABLE")

    # N
    def test_n_cross_modal_validity(self):
        class Tracker:
            def __init__(self, target):
                self._t = target

            def pointed_element(self, _vb):
                return self._t
        target = {"element": "Save", "kind": "uia",
                  "window": ("app-a", "Window A"),
                  "provenance": "hand+uia:Save"}
        # Both fresh and same window -> valid.
        hb = _hbus()
        hb.refresh()
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                               hand_bus=hb, hand_tracker=Tracker(target))
        self.assertTrue(ws.correlation("hand->visual")["valid"])
        # Target belongs to a different window -> rejected.
        hb2 = _hbus()
        hb2.refresh()
        bad = dict(target, window=("other", "Elsewhere"))
        ws2 = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                                hand_bus=hb2,
                                hand_tracker=Tracker(bad))
        self.assertFalse(ws2.correlation("hand->visual")["valid"])
        # Stale hand -> rejected even with a matching target.
        ws3 = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                                hand_bus=_hbus(fresh=False),
                                hand_tracker=Tracker(target))
        self.assertFalse(ws3.correlation("hand->visual")["valid"])

    # O/P
    def test_o_modality_missing_degrades(self):
        ws = compose_workspace(bundle=_bundle())  # nothing wired
        self.assertTrue(ws.describe())
        self.assertIn("visual_unwired", ws.degraded)
        self.assertIn("hand_unwired", ws.degraded)

    def test_p_one_failure_does_not_destroy(self):
        class Boom:
            def __getattr__(self, n):
                raise RuntimeError("boom")
        ws = compose_workspace(bundle=_bundle(), visual_bus=Boom(),
                               hand_bus=Boom())
        self.assertIn("visual", ws.status()["sources"])
        self.assertIn("hand", ws.status()["sources"])

    # Q/R
    def test_q_capability(self):
        publish_workspace(compose_workspace(bundle=_bundle(),
                                            visual_bus=_vbus()))
        bus = CapabilityBus()
        r = bus.request("SHARED_WORKSPACE", {})
        self.assertTrue(r["success"])
        self.assertIn("status", r["data"])
        d = bus.request("SHARED_WORKSPACE", {"op": "describe"})
        self.assertTrue(d["success"])

    def test_r_primer_produces_workspace(self):
        hb = _hbus()
        hb.refresh()
        primer = ContextPrimer(visual_bus=_vbus(), hand_bus=hb)
        b = primer.prime("continue the work")
        self.assertIsNotNone(b.workspace)
        self.assertIs(b.workspace, last_workspace())
        self.assertTrue(b.sources_ok.get("workspace"))
        self.assertIn("Shared Workspace", b.describe())

    # S
    def test_s_no_execution_authority(self):
        src = open(r"E:\Dude\dude\core\orchestrator\shared_workspace.py",
                   encoding="utf-8").read()
        for bad in ("IntelligenceRouter", "TaskEngine", "ActionExecutor",
                    "VerificationEngine", "RecoveryEngine"):
            self.assertNotIn(bad, src, bad)
        ws = SharedWorkspace()
        for attr in ("route", "plan", "execute", "act", "click", "recover",
                     "brain", "router", "engine"):
            self.assertFalse(hasattr(ws, attr), attr)

    # T/U
    def test_t_no_raw_media(self):
        hb = _hbus()
        hb.refresh()
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                               hand_bus=hb)
        for v in vars(ws).values():
            self.assertNotIsInstance(v, (bytes, bytearray))
        import numpy
        for v in vars(ws).values():
            self.assertNotIsInstance(v, numpy.ndarray)
        src = open(r"E:\Dude\dude\core\orchestrator\shared_workspace.py",
                   encoding="utf-8").read()
        for bad in ("imwrite", "VideoWriter", "sqlite3", "open("):
            self.assertNotIn(bad, src, bad)

    def test_u_no_second_store(self):
        # The workspace keeps no persistent store; only the in-memory
        # current-slot holder exists.
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus())
        self.assertFalse(hasattr(ws, "_db"))
        self.assertFalse(hasattr(ws, "conn"))

    # V
    def test_v_single_cognitive_authority(self):
        cfd_src = open(
            r"E:\Dude\dude\core\orchestrator\goal_executor.py",
            encoding="utf-8").read()
        # The workspace is passed as context only.
        self.assertIn('cognitive_context["shared_workspace"]', cfd_src)
        self.assertNotIn("class SkillWorkspaceBrain", cfd_src)

    # W
    # X: deictic referent resolution (evidence-gated)
    def test_x_referent_from_pointed_element(self):
        class Tracker:
            def pointed_element(self, _vb):
                return {"element": "Save", "kind": "uia",
                        "window": ("app-a", "Window A"),
                        "provenance": "hand+uia:Save"}
        hb = _hbus()
        # Place fresh hand evidence at the moment of the assertion so the
        # result depends on the logic, not on how loaded the machine is.
        hand = hb.refresh()
        self.assertIsNotNone(hand, "hand evidence must be fresh")
        self.assertFalse(hand.is_stale(1.0), "hand evidence must be fresh")
        ws = compose_workspace(
            bundle=_bundle(), visual_bus=_vbus(), hand_bus=hb,
            hand_tracker=Tracker(), transcript="what is this")
        # Preconditions the referent depends on (asserted, not assumed).
        self.assertTrue(ws.hand.fresh, "workspace must see fresh hand")
        self.assertTrue(ws.visual.fresh, "workspace must see fresh visual")
        self.assertEqual(ws.pointed_element.get("element"), "Save")
        self.assertTrue(ws.correlation("hand->visual")["valid"])
        # The actual behaviour under test.
        ref = ws.correlation("deixis_referent")
        self.assertTrue(ref["valid"])
        self.assertEqual(ws.referent["kind"], "pointed_element")
        self.assertEqual(ws.referent["name"], "Save")
        self.assertFalse(ws.referent_ambiguous)

    def test_y_referent_ambiguous_not_guessed(self):
        # No hand evidence, but a fresh visual state with focus AND a
        # window: two candidate classes -> ambiguity, no choice.
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                               transcript="what is this")
        self.assertTrue(ws.referent_ambiguous)
        self.assertIsNone(ws.referent)
        self.assertFalse(ws.correlation("deixis_referent")["valid"])

    def test_z_referent_none_without_deixis_or_evidence(self):
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                               transcript="organize the downloads folder")
        self.assertIsNone(ws.referent)
        self.assertFalse(ws.referent_ambiguous)
        # No deictic token -> no correlation invented.
        self.assertIsNone(ws.correlation("deixis_referent"))
        # Anaphoric "it" is left to the front door (not resolved here).
        ws2 = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                                transcript="tell me about it")
        self.assertIsNone(ws2.referent)

    def test_z2_referent_requires_fresh_visual(self):
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus(False),
                               transcript="show this")
        self.assertIsNone(ws.referent)
        self.assertFalse(ws.correlation("deixis_referent")["valid"])

    # Continuity (bounded, same-session, never a store)
    def test_continuity_carried_within_window(self):
        first = compose_workspace(bundle=_bundle(goal="write the report"),
                                  visual_bus=_vbus())
        second = compose_workspace(bundle=_bundle(goal="continue it"),
                                   visual_bus=_vbus(), previous=first)
        self.assertTrue(second.continuity.get("continued"))
        self.assertIn("write the report", second.continuity["from_goal"])
        self.assertLess(second.continuity["age_s"], 5)

    def test_continuity_expires(self):
        first = compose_workspace(bundle=_bundle(goal="old work"),
                                  visual_bus=_vbus())
        first.captured_at = time.time() - 5000  # outside the window
        second = compose_workspace(bundle=_bundle(goal="new work"),
                                   visual_bus=_vbus(), previous=first)
        self.assertFalse(second.continuity.get("continued"))

    def test_referent_does_not_choose_actions(self):
        ws = compose_workspace(bundle=_bundle(), visual_bus=_vbus(),
                               transcript="what is this")
        # Ambiguous here; ensure nothing actionable was attached.
        for attr in ("action", "plan", "steps", "executor"):
            self.assertFalse(hasattr(ws, attr), attr)

    def test_w_no_growth_over_repeated_updates(self):
        hb = _hbus()
        primer = ContextPrimer(visual_bus=_vbus(), hand_bus=hb)
        for _ in range(50):
            hb.refresh()
            b = primer.prime("repeat work")
            self.assertIsNotNone(b.workspace)
        ws = b.workspace
        self.assertLessEqual(len(ws.correlations), 8)
        self.assertLessEqual(len(ws.degraded), 8)
        self.assertLessEqual(len(str(ws.describe())), 600)


if __name__ == "__main__":
    unittest.main()
