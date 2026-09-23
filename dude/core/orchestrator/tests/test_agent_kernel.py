"""Focused tests: AgentState facade + CapabilityBus gateway.

Covers: state round-trip, bus routing to real owners (offline-safe
subset), capability discovery connection, unknown-goal partial planning
(no NO_SOLUTION hard boundary), model action-type validation.
"""
import sys
import unittest

sys.path.insert(0, r'E:\Dude\dude')


class TestAgentState(unittest.TestCase):
    def test_goal_lifecycle(self):
        from core.agent_state import AgentState
        st = AgentState()
        st.set_goal("open Word and write something")
        self.assertEqual(st.get_goal(), "open Word and write something")
        st.update_task(task="word-task", progress="Word open")
        st.set_step("Typing paragraph", 2, 4)
        st.set_screen_context(app="Word", window="Document1",
                              focus="editor", ocr="hello world")
        st.set_verification(True, "ocr matched")
        st.set_result("Done. open Word", confidence=0.8,
                      provenance="kernel")
        snap = st.snapshot()
        self.assertEqual(snap["step"], "Typing paragraph")
        self.assertEqual(snap["step_index"], 2)
        self.assertEqual(snap["ocr"], "hello world")
        self.assertTrue(snap["verified"])
        st.clear_active_goal()
        snap = st.snapshot()
        self.assertEqual(snap["goal"], "")
        self.assertEqual(snap["result"], "Done. open Word")

    def test_correction_kept(self):
        from core.agent_state import AgentState
        st = AgentState()
        st.set_goal("write report")
        st.set_correction("use the other workbook")
        self.assertIn("other workbook", st.snapshot()["correction"])


class TestCapabilityBus(unittest.TestCase):
    def test_unknown_capability(self):
        from core.capability_bus import CapabilityBus
        bus = CapabilityBus()
        r = bus.request("NOPE_NOT_REAL", {})
        self.assertFalse(r["success"])
        self.assertIn("unknown capability", r["error"])

    def test_no_refs_unavailable(self):
        from core.capability_bus import CapabilityBus
        bus = CapabilityBus()
        # Ref-free capabilities that need live owners must report
        # unavailability honestly (OCR is exempt: tesseract reads the
        # live screen standalone).
        for cap in ("SCREEN_STATE", "UIA", "VISION", "MEMORY_RECALL",
                    "JOB_MEMORY", "TOOLS"):
            r = bus.request(cap, {})
            self.assertFalse(r["success"])
            self.assertTrue(r["error"])
            for k in ("success", "capability", "data", "evidence",
                      "provenance", "error", "confidence"):
                self.assertIn(k, r)

    def test_discovery_uses_registry(self):
        from core.capability_bus import CapabilityBus
        bus = CapabilityBus()
        r = bus.request("CAPABILITY_DISCOVERY", {"capability": "launch"})
        self.assertTrue(r["success"])
        names = [d["name"] for d in r["data"]]
        self.assertIn("open_app", names)

    def test_discovery_unknown(self):
        from core.capability_bus import CapabilityBus
        bus = CapabilityBus()
        r = bus.request("CAPABILITY_DISCOVERY",
                        {"capability": "teleport"})
        self.assertFalse(r["success"])

    def test_memory_recall_offline(self):
        from core.capability_bus import CapabilityBus
        from core.memory import Memory
        bus = CapabilityBus()
        bus.attach(memory=Memory())
        r = bus.request("MEMORY_RECALL", {"query": "word report",
                                          "limit": 2})
        self.assertIn("success", r)
        self.assertEqual(r["provenance"], "memory")

    def test_job_memory_roundtrip(self):
        from core.capability_bus import CapabilityBus
        from core.memory import Memory
        from core import job_memory as _jm
        m = Memory()
        _jm.upsert_job(m, "BUS_SMOKE_JOB", purpose="bus probe",
                       procedure=["Do one thing"])
        bus = CapabilityBus()
        bus.attach(memory=m)
        try:
            r = bus.request("JOB_MEMORY", {"query": "bus smoke"})
            self.assertTrue(r["success"])
            self.assertEqual(r["data"][0]["name"], "BUS_SMOKE_JOB")
        finally:
            import json as _json
            raw = m.get_state("jobs_index", "")
            d = _json.loads(raw) if raw else {}
            d.pop("BUS_SMOKE_JOB", None)
            m.set_state("jobs_index", _json.dumps(d))


class TestGeneralPlanning(unittest.TestCase):
    def test_compound_plans_fully(self):
        from core.orchestrator.state import (
            TaskState, PerceptionSnapshot, PerceptionLevel)
        from core.orchestrator.intelligence_router import IntelligenceRouter
        router = IntelligenceRouter()
        snap = PerceptionSnapshot(
            active_app="unknown",
            capture_method=PerceptionLevel.LEVEL_2_UIA_TREE)
        text = ("open Word document and write some big sentence "
                "and close it dont save it")
        ts = TaskState(goal=text)
        ts.user_interrupt = text
        r = router.route(task_state=ts, perception=snap, user_intent=text)
        self.assertEqual(r.decision.value, "local_reasoning")
        kinds = [s.action_type for s in r.plan.steps]
        self.assertIn("open_app", kinds)
        self.assertIn("type_text", kinds)
        self.assertIn("close_app", kinds)

    def test_unplanned_remainder_flows(self):
        from core.orchestrator.state import (
            TaskState, PerceptionSnapshot, PerceptionLevel)
        from core.orchestrator.intelligence_router import IntelligenceRouter
        router = IntelligenceRouter()
        snap = PerceptionSnapshot(
            active_app="unknown",
            capture_method=PerceptionLevel.LEVEL_2_UIA_TREE)
        text = "open Word and juggle flaming torches"
        ts = TaskState(goal=text)
        ts.user_interrupt = text
        r = router.route(task_state=ts, perception=snap, user_intent=text)
        # Must NOT be a bare skill swallowing the goal, and must NOT be a
        # hard NO_SOLUTION when part of it is plannable.
        if r.decision.value == "local_reasoning":
            unp = getattr(r, "unplanned_texts", None) or []
            self.assertTrue(unp or r.plan.steps)
        self.assertNotEqual(
            (r.decision.value, [s.action_type for s in (r.plan.steps if r.plan else [])]),
            ("deterministic_skill", ["open_app"]))

    def test_model_action_validation(self):
        from core.orchestrator.task_engine import TaskEngine
        self.assertTrue(TaskEngine._valid_model_action(object(), "open_app"))
        self.assertTrue(TaskEngine._valid_model_action(object(), "type_text"))
        self.assertFalse(
            TaskEngine._valid_model_action(object(), "fly_to_moon"))


if __name__ == "__main__":
    unittest.main()
