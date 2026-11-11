"""Phase 13 generic learning-behavior tests.

No applications, no UI, no network, no model calls. All collaborators
are fakes or temp SQLite stores; action verbs are generic OS-level
capabilities (create_folder/write_file/read_file), never app-specific.
"""
import os
import tempfile
import unittest
from unittest import mock

from core.orchestrator.procedure_store import ProcedureStore
from core.orchestrator.procedure_adaptation import (
    ProcedureAdaptationManager,
    ProcedureAdaptationStore,
)
from core.orchestrator.procedure_learner import ProcedureLearner
from core.orchestrator.state import (
    RiskLevel,
    SubGoal,
    TaskState,
    VerificationMethod,
    VerificationResult,
)


def _folder_state(store_goal, folder):
    """A verified DONE task state for 'create a folder called <name>'."""
    ts = TaskState(goal=store_goal)
    ts.relevant_memory = None
    ts.learned_procedure = None
    ts.current_application = "test-harness"
    sg = SubGoal(
        description="Create directory %s" % folder,
        intent=store_goal,
        action_type="create_folder",
        target_description=folder,
        expected_result=folder,
        verification_method=VerificationMethod.FILE_EXISTS,
        risk_level=RiskLevel.LOW,
        order=0,
    )
    ts.subgoals = [sg]
    ts.current_step = 1
    from core.orchestrator.state import StepResult
    ts.completed_steps = [StepResult(subgoal=sg, success=True)]
    ts.verification_result = VerificationResult(
        success=True, method=VerificationMethod.FILE_EXISTS,
        evidence=folder, confidence=1.0)
    return ts


class _FakeNS:
    def __init__(self, name):
        self.name = name


class Phase13LearningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "procedures.db")
        self.adb = os.path.join(self.tmp.name, "adaptations.db")
        self.store = ProcedureStore(db_path=self.db)
        self.learner = ProcedureLearner(
            procedure_store=self.store, enable_learning=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _two_successes(self, goal_a, goal_b):
        vr = VerificationResult(success=True,
                                method=VerificationMethod.FILE_EXISTS)
        from core.orchestrator.state import OrchestratorState
        self.learner.on_task_completed(
            task_state=_folder_state(goal_a, "phase13-alpha"),
            perception_before=None, perception_after=None,
            verification_result=vr, orchestrator_state=OrchestratorState.DONE)
        self.learner.on_task_completed(
            task_state=_folder_state(goal_b, "phase13-beta"),
            perception_before=None, perception_after=None,
            verification_result=vr, orchestrator_state=OrchestratorState.DONE)

    def test_a_verified_success_creates_reusable_procedure(self):
        self._two_successes(
            "create a folder called phase13-alpha on the Desktop",
            "create a folder called phase13-beta on the Desktop")
        procs = self.store.find_by_goal("folder", 0.0)
        self.assertEqual(len(procs), 1)
        proc = procs[0]
        self.assertEqual(proc.confidence, 0.7)
        self.assertEqual(proc.source, "verified_trace")
        self.assertGreaterEqual(proc.success_count, 0)
        # Generalized: the literal folder names must not survive.
        blob = (proc.goal or "") + str(proc.steps)
        self.assertNotIn("phase13-alpha", blob)
        self.assertNotIn("phase13-beta", blob)
        self.assertIn("folder_name", blob)

    def test_b_failed_experience_never_promotes(self):
        ts = _folder_state("create a folder called phase13-x on the Desktop",
                           "phase13-x")
        ts.failure_reason = "target already exists"
        self.learner.on_verification_failed(
            task_state=ts, perception=None,
            verification_result=VerificationResult(success=False))
        self.assertEqual(self.store.find_by_goal("folder", 0.0), [])
        fails = self.store.recent_failures(
            "create a folder called phase13-x on the Desktop")
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0]["kind"], "failure")
        self.assertIn("already exists", fails[0]["reason"])

    def test_c_recovery_success_keeps_failure_plus_outcome(self):
        ts = _folder_state("create a folder called phase13-x on the Desktop",
                           "phase13-x")
        ts.failure_reason = "target already exists"
        ts.recovery_history = [{"phase": "retry", "action": "reuse existing",
                                "success": True}]
        self.learner.on_verification_failed(
            task_state=ts, perception=None,
            verification_result=VerificationResult(success=False))
        fails = self.store.recent_failures(
            "create a folder called phase13-x on the Desktop")
        self.assertTrue(fails)
        self.assertEqual(fails[0]["recovery_action"], "reuse existing")
        vr = VerificationResult(success=True,
                                method=VerificationMethod.FILE_EXISTS)
        from core.orchestrator.state import OrchestratorState
        self.learner.on_task_completed(
            task_state=_folder_state(
                "create a folder called phase13-x on the Desktop",
                "phase13-x"),
            perception_before=None, perception_after=None,
            verification_result=vr, orchestrator_state=OrchestratorState.DONE)
        # Recovery-then-success still counts toward promotion evidence.
        self.assertTrue(self.store.recent_failures(
            "create a folder called phase13-x on the Desktop"))

    def test_d_similar_goal_retrieves_prior_procedure(self):
        self._two_successes(
            "create a folder called phase13-alpha on the Desktop",
            "create a folder called phase13-beta on the Desktop")
        hits = self.store.find_by_normalized_goal(
            "create a folder called phase13-gamma on the Desktop", 0.5)
        self.assertTrue(hits)
        self.assertIn("folder_name", hits[0].goal)

    def test_e_conditions_and_negative_gate_before_reuse(self):
        self._two_successes(
            "create a folder called phase13-alpha on the Desktop",
            "create a folder called phase13-beta on the Desktop")
        proc = self.store.find_by_goal("folder", 0.0)[0]
        # Confidence gate: three failures drop 0.7 -> 0.4, below reuse bar.
        self.store.record_failure(proc.id)
        self.store.record_failure(proc.id)
        self.assertTrue(self.store.find_by_goal("folder", 0.5))
        self.store.record_failure(proc.id)
        self.assertEqual(self.store.find_by_goal("folder", 0.5), [])
        # Negative gate trips even earlier: two failure events suffice.
        self.assertFalse(self.store.has_recent_failures(
            "create a folder called phase13-zeta on the Desktop"))
        self.store.record_event(
            kind="failure",
            goal_text="create a folder called phase13-zeta on the Desktop",
            reason="boom")
        self.assertFalse(self.store.has_recent_failures(
            "create a folder called phase13-zeta on the Desktop"))
        self.store.record_event(
            kind="failure",
            goal_text="create a folder called phase13-zeta on the Desktop",
            reason="boom again")
        self.assertTrue(self.store.has_recent_failures(
            "create a folder called phase13-zeta on the Desktop"))

    def test_f_adaptation_to_changed_environment(self):
        mgr = ProcedureAdaptationManager(
            procedure_store=self.store,
            adaptation_store=ProcedureAdaptationStore(db_path=self.adb))
        from core.orchestrator.state import Procedure
        base = Procedure(
            goal="create a folder called {folder_name} on the Desktop",
            steps=[{"description": "Create directory",
                    "intent": "g", "action_type": "create_folder",
                    "target": "{folder_name}", "expected": "{folder_name}",
                    "verification_method": "FILE_EXISTS"}],
            parameters=[],
            confidence=0.7,
            source="verified_trace",
        )
        base_id = self.store.save(base)

        class Ctl:
            def __init__(self, n):
                self.name = n

        class Snap:
            controls = [Ctl("OtherDialog")]

        adapted, record = mgr.adapt_plan(
            base, "create a folder called phase13-new on the Desktop",
            Snap())
        self.assertTrue(adapted)
        self.assertIn("phase13-new", adapted[0].target_description)
        self.assertNotIn("phase13-alpha", adapted[0].target_description)
        self.assertEqual(record["trigger"], "parameter_changed")
        # Unmatched target is flagged, not silently kept.
        self.assertTrue(any(
            m.get("change") == "target-unmatched-live-ui"
            for m in record["step_modifications"]))

        ts = TaskState(goal="create a folder called phase13-new")
        ts.subgoals = []
        ts.current_step = 0
        self.assertTrue(mgr.execute_adaptation(record, ts, adapted))
        self.assertEqual([s.order for s in ts.subgoals],
                         list(range(len(ts.subgoals))))
        # Not yet executed -> verify must refuse.
        self.assertFalse(mgr.verify_adaptation(record, ts))
        # Mark the adapted steps verified-done -> verify passes.
        from core.orchestrator.state import StepResult
        for sg in ts.subgoals:
            ts.completed_steps.append(StepResult(subgoal=sg, success=True))
        self.assertTrue(mgr.verify_adaptation(record, ts))
        # A failed step poisons verification.
        ts.completed_steps[-1].success = False
        self.assertFalse(mgr.verify_adaptation(record, ts))

    def test_g_repeated_success_raises_evidence(self):
        self._two_successes(
            "create a folder called phase13-alpha on the Desktop",
            "create a folder called phase13-beta on the Desktop")
        proc = self.store.find_by_goal("folder", 0.0)[0]
        self.store.record_success(proc.id)
        self.store.record_success(proc.id)
        self.store.record_success(proc.id)
        proc2 = self.store.get(proc.id)
        self.assertEqual(proc2.success_count, proc.success_count + 3)
        self.assertAlmostEqual(proc2.confidence,
                               min(1.0, proc.confidence + 0.15))

    def test_h_repeated_failure_reduces_confidence(self):
        self._two_successes(
            "create a folder called phase13-alpha on the Desktop",
            "create a folder called phase13-beta on the Desktop")
        proc = self.store.find_by_goal("folder", 0.0)[0]
        before = proc.confidence
        self.store.record_failure(proc.id)
        after = self.store.get(proc.id)
        self.assertEqual(after.failure_count, proc.failure_count + 1)
        self.assertLess(after.confidence, before)

    def test_i_learned_knowledge_is_application_agnostic(self):
        self._two_successes(
            "create a folder called phase13-alpha on the Desktop",
            "create a folder called phase13-beta on the Desktop")
        proc = self.store.find_by_goal("folder", 0.0)[0]
        blob = ((proc.goal or "") + str(proc.steps)
                + str(proc.parameters)).lower()
        for app in ("calculator", "notepad", "word", "whatsapp", "comet",
                    "explorer", "powershell"):
            self.assertNotIn(app, blob)
        allowed = {"create_folder", "create_directory", "make_folder",
                   "write_file", "create_file", "read_file", "open_app",
                   "close_app", "click", "type_text", "hotkey", "scroll",
                   "screenshot", "read_text", "execute"}
        for step in proc.steps:
            self.assertIn(str(step.get("action_type", "")).lower(), allowed)

    def test_j_provenance_preserved(self):
        self._two_successes(
            "create a folder called phase13-alpha on the Desktop",
            "create a folder called phase13-beta on the Desktop")
        proc = self.store.find_by_goal("folder", 0.0)[0]
        self.assertEqual(proc.source, "verified_trace")
        self.assertTrue(proc.created_at)
        self.assertTrue(proc.updated_at)
        self.assertGreaterEqual(proc.success_count, 0)

    def test_k_unverified_work_never_promotes(self):
        vr = VerificationResult(success=False)
        from core.orchestrator.state import OrchestratorState
        self.learner.on_task_completed(
            task_state=_folder_state("create a folder called q on x",
                                     "phase13-q"),
            perception_before=None, perception_after=None,
            verification_result=vr, orchestrator_state=OrchestratorState.DONE)
        self.assertEqual(self.store.find_by_goal("folder", 0.0), [])
        self.assertEqual(
            [e for e in self.store.recent_failures(
                "create a folder called q on x")], [])

    def test_promote_renders_inspectable_note(self):
        with mock.patch(
                "core.knowledge.get_knowledge") as gk:
            self._two_successes(
                "create a folder called phase13-alpha on the Desktop",
                "create a folder called phase13-beta on the Desktop")
            self.assertTrue(gk.return_value.save_note.called)
            _args, _kwargs = gk.return_value.save_note.call_args
            body = (_args[1] if len(_args) > 1 else "") + str(_kwargs)
            self.assertIn("phase13", body.lower())


if __name__ == "__main__":
    unittest.main()
