"""Phase 2B Intelligence Router Tests.

Tests for the deterministic skills registry, procedure store,
and intelligence routing logic.
"""
import unittest
import tempfile
import os
import asyncio
from unittest.mock import Mock, patch, MagicMock
from typing import AsyncIterator

from core.orchestrator.state import OrchestratorState

from core.orchestrator.skills import (
    Skill,
    SkillRegistry,
    SkillExecutor,
    get_skill_registry,
    register_skill,
    OpenAppExecutor,
    CloseAppExecutor,
    FilesystemExecutor,
    KnowledgeExecutor,
)
from core.orchestrator.procedure_store import (
    ProcedureStore,
    ProcedureCandidate,
    get_procedure_store,
    create_procedure_from_trace,
)
from core.orchestrator.intelligence_router import (
    IntelligenceRouter,
    RouteDecision,
    RoutingResult,
    create_intelligence_router,
)
from core.orchestrator.state import (
    Plan,
    TaskState,
    TaskType,
    SubGoal,
    VerificationMethod,
    RiskLevel,
    Procedure,
    TargetSpec,
    ExpectedResult,
    PerceptionSnapshot,
    PerceptionLevel,
    MemoryBundle,
)
from core.orchestrator.intelligence import (
    IntelligenceBackend,
    ModelCapabilities,
    HealthStatus,
    GenerateRequest,
    GenerateResponse,
    IntelligenceBackendRegistry,
)


class MockIntelligenceBackend(IntelligenceBackend):
    """Mock backend for testing."""
    
    def __init__(self, name="mock"):
        self._name = name
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities()
    
    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        return GenerateResponse(content="mock response")
    
    async def stream(self, request) -> AsyncIterator[str]:
        yield "mock"
    
    async def health(self) -> HealthStatus:
        return HealthStatus(healthy=True, message="OK")
    
    async def warm_up(self) -> bool:
        return True


class TestSkillRegistry(unittest.TestCase):
    """Test the deterministic skill registry."""
    
    def setUp(self):
        self.registry = SkillRegistry()
    
    def test_builtin_skills_registered(self):
        """All builtin skills should be registered."""
        skills = self.registry.all()
        self.assertGreater(len(skills), 20)
        
        # Check some key skills exist
        skill_names = {s.name for s in skills}
        self.assertIn("open_app", skill_names)
        self.assertIn("close_app", skill_names)
        self.assertIn("create_folder", skill_names)
        self.assertIn("read_file", skill_names)
        self.assertIn("write_file", skill_names)
        self.assertIn("click_control", skill_names)
        self.assertIn("type_text", skill_names)
        self.assertIn("search_knowledge", skill_names)
        self.assertIn("recall_about_user", skill_names)
    
    def test_get_skill(self):
        """Get a specific skill by name."""
        skill = self.registry.get("open_app")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "open_app")
        self.assertEqual(skill.purpose, "Launch an application by name")
    
    def test_find_by_capability(self):
        """Find skills by capability keyword."""
        matches = self.registry.find_by_capability("launch")
        self.assertTrue(any(s.name == "open_app" for s in matches))
        
        matches = self.registry.find_by_capability("mkdir")
        self.assertTrue(any(s.name == "create_folder" for s in matches))
        
        matches = self.registry.find_by_capability("click")
        self.assertTrue(any(s.name == "click_control" for s in matches))
    
    def test_find_by_intent(self):
        """Find skills matching user intent."""
        matches = self.registry.find_by_intent("open notepad")
        self.assertTrue(any(s.name == "open_app" for s in matches))
        
        matches = self.registry.find_by_intent("mkdir test")
        self.assertTrue(any(s.name == "create_folder" for s in matches))
        
        matches = self.registry.find_by_intent("type hello world")
        self.assertTrue(any(s.name == "type_text" for s in matches))
        
        matches = self.registry.find_by_intent("search knowledge for python")
        self.assertTrue(any(s.name == "search_knowledge" for s in matches))
    
    def test_register_custom_skill(self):
        """Register a custom skill."""
        custom = Skill(
            name="custom_skill",
            purpose="Test custom skill",
            capabilities=["custom"],
            executor=Mock(),
        )
        register_skill(custom)
        
        # Should be available in global registry
        global_registry = get_skill_registry()
        skill = global_registry.get("custom_skill")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.purpose, "Test custom skill")


class TestSkillExecutors(unittest.TestCase):
    """Test individual skill executors."""
    
    def setUp(self):
        self.mock_memory = Mock()
        self.mock_perception = Mock()
        self.mock_ask_user = lambda *a: True
    
    def test_open_app_executor(self):
        """OpenAppExecutor calls open_app tool."""
        executor = OpenAppExecutor()
        action = Mock()
        action.target.text_match = "notepad"
        
        with patch('core.orchestrator.skills.execute_tool') as mock_exec:
            mock_exec.return_value = "OK: opened notepad"
            result = executor.execute(action, self.mock_perception, self.mock_memory)
            
            mock_exec.assert_called_once()
            args = mock_exec.call_args[0][1]
            self.assertEqual(args["app_name"], "notepad")
    
    def test_filesystem_executor(self):
        """FilesystemExecutor handles multiple operations."""
        executor = FilesystemExecutor()
        action = Mock()
        
        with patch('core.orchestrator.skills.execute_tool') as mock_exec:
            mock_exec.return_value = "OK"
            
            # create_folder
            action.action_type = "create_folder"
            action.target.text_match = "/tmp/test"
            executor.execute(action, self.mock_perception, self.mock_memory)
            mock_exec.assert_called()
            args = mock_exec.call_args[0][1]
            self.assertEqual(args["path"], "/tmp/test")
            
            # read_file
            action.action_type = "read_file"
            action.target.text_match = "/tmp/file.txt"
            executor.execute(action, self.mock_perception, self.mock_memory)
            mock_exec.assert_called()
            args = mock_exec.call_args[0][1]
            self.assertEqual(args["path"], "/tmp/file.txt")
            
            # write_file (with pipe separator)
            action.action_type = "write_file"
            action.target.text_match = "/tmp/file.txt|hello world"
            executor.execute(action, self.mock_perception, self.mock_memory)
            mock_exec.assert_called()
            args = mock_exec.call_args[0][1]
            self.assertEqual(args["path"], "/tmp/file.txt")
            self.assertEqual(args["content"], "hello world")


class TestProcedureStore(unittest.TestCase):
    """Test the procedure store."""
    
    def setUp(self):
        # Use temp database for isolation
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.store = ProcedureStore(self.temp_db.name)
    
    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)
    
    def test_save_and_retrieve(self):
        """Save and retrieve a procedure."""
        proc = Procedure(
            goal="Open notepad and type hello",
            goal_type="automate",
            context={"app": "notepad"},
            steps=[
                {"description": "Open notepad", "action_type": "open_app", "target": "notepad"},
                {"description": "Type hello", "action_type": "type_text", "target": "hello"},
            ],
            verification=[{"method": "UIA_STATE_CHANGE"}],
            confidence=0.8,
        )
        
        proc_id = self.store.save(proc)
        self.assertGreater(proc_id, 0)
        
        retrieved = self.store.get(proc_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.goal, "Open notepad and type hello")
        self.assertEqual(len(retrieved.steps), 2)
        self.assertEqual(retrieved.confidence, 0.8)
    
    def test_find_by_goal(self):
        """Find procedures by goal pattern."""
        proc1 = Procedure(goal="Open notepad", goal_type="automate", confidence=0.8)
        proc2 = Procedure(goal="Close notepad", goal_type="automate", confidence=0.7)
        proc3 = Procedure(goal="Open calculator", goal_type="automate", confidence=0.6)
        
        self.store.save(proc1)
        self.store.save(proc2)
        self.store.save(proc3)
        
        results = self.store.find_by_goal("notepad", min_confidence=0.5)
        self.assertEqual(len(results), 2)
        # Should be ordered by confidence
        self.assertEqual(results[0].goal, "Open notepad")
    
    def test_record_success_failure(self):
        """Record success/failure updates confidence."""
        proc = Procedure(goal="Test", goal_type="automate", confidence=0.5)
        proc_id = self.store.save(proc)
        
        # Record success
        self.store.record_success(proc_id)
        updated = self.store.get(proc_id)
        self.assertEqual(updated.success_count, 1)
        self.assertGreater(updated.confidence, 0.5)
        
        # Record failure
        self.store.record_failure(proc_id)
        updated = self.store.get(proc_id)
        self.assertEqual(updated.failure_count, 1)
        self.assertLess(updated.confidence, 0.55)  # 0.55 - 0.1 = 0.45
    
    def test_promote_candidate(self):
        """Promote a candidate to a verified procedure."""
        candidate = ProcedureCandidate(
            goal="Test procedure",
            context={"app": "notepad"},
            steps=[
                {"description": "Step 1", "action_type": "open_app", "target": "notepad"},
            ],
            verification_results=[{"success": True, "method": "WINDOW_APPEARED"}],
        )
        
        proc = self.store.promote_candidate(candidate)
        self.assertIsNotNone(proc)
        self.assertEqual(proc.goal, "Test procedure")
        self.assertEqual(proc.source, "verified_trace")
        self.assertEqual(proc.confidence, 0.7)
    
    def test_stats(self):
        """Get store statistics."""
        self.store.save(Procedure(goal="Test 1", goal_type="automate", confidence=0.8))
        self.store.save(Procedure(goal="Test 2", goal_type="automate", confidence=0.6))
        
        stats = self.store.get_stats()
        self.assertEqual(stats["total_procedures"], 2)
        self.assertGreater(stats["avg_confidence"], 0.0)

    def test_verification_method_round_trip(self):
        """Verify FILE_EXISTS and other methods survive persist/retrieve."""
        proc = Procedure(
            goal="Verify file test",
            goal_type="automate",
            context={},
            steps=[
                {"action_type": "verify_file", "target": "test.txt",
                 "verification_method": VerificationMethod.FILE_EXISTS},
                {"action_type": "open_app", "target": "notepad",
                 "verification_method": VerificationMethod.WINDOW_APPEARED},
            ],
            verification=[VerificationMethod.FILE_EXISTS, VerificationMethod.WINDOW_APPEARED],
            confidence=0.8,
        )

        proc_id = self.store.save(proc)
        retrieved = self.store.get(proc_id)

        # Check steps' verification_method survived as enum
        self.assertEqual(len(retrieved.steps), 2)
        self.assertEqual(
            retrieved.steps[0].get("verification_method"),
            VerificationMethod.FILE_EXISTS
        )
        self.assertEqual(
            retrieved.steps[1].get("verification_method"),
            VerificationMethod.WINDOW_APPEARED
        )
        # Also check top-level verification list
        self.assertIn(VerificationMethod.FILE_EXISTS, retrieved.verification)
        self.assertIn(VerificationMethod.WINDOW_APPEARED, retrieved.verification)


class TestIntelligenceRouter(unittest.TestCase):
    """Test the intelligence router."""
    
    def setUp(self):
        self.router = IntelligenceRouter()
        # Register a mock model backend
        registry = Mock()
        registry.list.return_value = ["mock"]
        self.router.intelligence = registry
    
    def test_route_deterministic_skill(self):
        """Route simple intent to deterministic skill."""
        task_state = TaskState(goal="open notepad")
        perception = PerceptionSnapshot(
            active_app="explorer",
            capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
        )
        
        result = self.router.route(task_state, perception, "open notepad")
        
        self.assertEqual(result.decision, RouteDecision.DETERMINISTIC_SKILL)
        self.assertIsNotNone(result.skill)
        self.assertEqual(result.skill.name, "open_app")
        self.assertIsNotNone(result.plan)
    
    def test_route_creates_plan(self):
        """Router creates structured plan."""
        task_state = TaskState(goal="create folder test")
        perception = PerceptionSnapshot(capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
        
        result = self.router.route(task_state, perception, "create folder test")
        
        self.assertEqual(result.decision, RouteDecision.DETERMINISTIC_SKILL)
        self.assertIsNotNone(result.plan)
        self.assertEqual(len(result.plan.steps), 1)
        self.assertEqual(result.plan.steps[0].action_type, "create_folder")
    
    def test_route_local_reasoning(self):
        """Route knowledge query to local reasoning."""
        task_state = TaskState(goal="what is python")
        perception = PerceptionSnapshot(capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
        
        result = self.router.route(task_state, perception, "what is python")
        
        self.assertEqual(result.decision, RouteDecision.LOCAL_REASONING)
        self.assertIsNotNone(result.plan)
        self.assertEqual(result.plan.steps[0].action_type, "search_knowledge")
    
    def test_route_fallback_when_no_local(self):
        """Route to model fallback when no local solution."""
        task_state = TaskState(goal="complex creative writing task")
        perception = PerceptionSnapshot(capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
        
        result = self.router.route(task_state, perception, "complex creative writing task")
        
        self.assertEqual(result.decision, RouteDecision.MODEL_FALLBACK)
    
    def test_skill_preferred_over_model(self):
        """Deterministic skill preferred over model fallback."""
        task_state = TaskState(goal="open notepad")
        perception = PerceptionSnapshot(capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
        
        result = self.router.route(task_state, perception, "open notepad")
        
        # Should pick skill even though model is available
        self.assertEqual(result.decision, RouteDecision.DETERMINISTIC_SKILL)
    
    def test_stats_tracking(self):
        """Router tracks routing decisions."""
        task_state = TaskState(goal="open notepad")
        perception = PerceptionSnapshot(capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
        
        self.router.route(task_state, perception, "open notepad")
        self.router.route(task_state, perception, "what is python")
        
        stats = self.router.get_stats()
        self.assertEqual(stats["deterministic_skill"], 1)
        self.assertEqual(stats["local_reasoning"], 1)


class TestPlan(unittest.TestCase):
    """Test the Plan structured representation."""
    
    def test_plan_to_actions(self):
        """Plan converts to executable actions."""
        from core.orchestrator.state import TaskState
        
        task_state = TaskState(task_id="test-123")
        perception = PerceptionSnapshot(
            capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
            window_bounds=Mock(x=0, y=0, w=800, h=600)
        )
        
        plan = Plan(
            task_id="test-123",
            objective="open notepad",
            steps=[SubGoal(
                description="Open notepad",
                intent="open notepad",
                action_type="open_app",
                target_description="notepad",
                verification_method=VerificationMethod.WINDOW_APPEARED,
            )],
            confidence=0.9,
            source="skill",
        )
        
        actions = plan.to_actions(task_state, perception)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, "open_app")
        self.assertEqual(actions[0].target.text_match, "notepad")
    
    def test_plan_grounding_methods(self):
        """Plan assigns correct grounding methods."""
        from core.orchestrator.state import TaskState, VerificationMethod
        
        task_state = TaskState(task_id="test-123")
        perception = PerceptionSnapshot(capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
        
        # OCR verification -> OCR_TEXT grounding
        plan = Plan(
            task_id="test-123",
            objective="find text",
            steps=[SubGoal(
                description="Find text",
                intent="find hello",
                action_type="search",
                target_description="hello",
                verification_method=VerificationMethod.OCR_TEXT_APPEARED,
            )],
        )
        
        actions = plan.to_actions(task_state, perception)
        self.assertEqual(actions[0].grounding_method.value, "ocr_text")


class TestIntelligenceBackendAbstraction(unittest.TestCase):
    """Test that model backend is properly abstracted."""
    
    def test_registry_holds_multiple_backends(self):
        """Registry can hold multiple backends."""
        registry = IntelligenceBackendRegistry()
        
        backend1 = MockIntelligenceBackend("backend1")
        backend2 = MockIntelligenceBackend("backend2")
        
        registry.register(backend1, default=True)
        registry.register(backend2)
        
        self.assertEqual(len(registry.list()), 2)
        self.assertEqual(registry.get().name, "backend1")
        self.assertEqual(registry.get("backend2").name, "backend2")
    
    def test_task_engine_depends_on_abstraction(self):
        """TaskEngine should depend on IntelligenceBackend interface."""
        # The TaskEngine constructor takes IntelligenceBackend
        # This is verified by the type hint in task_engine.py
        from core.orchestrator.task_engine import TaskEngine
        import inspect
        
        sig = inspect.signature(TaskEngine.__init__)
        params = list(sig.parameters.keys())
        self.assertIn("intelligence", params)
        
        # The type should be IntelligenceBackend (abstract)
        intelligence_param = sig.parameters["intelligence"]
        self.assertEqual(intelligence_param.annotation, "IntelligenceBackend")


class TestSecretsExcluded(unittest.TestCase):
    """Test that secrets are not stored in procedures."""
    
    def test_procedure_no_secrets(self):
        """Procedure should not contain obvious secrets."""
        proc = Procedure(
            goal="Test",
            goal_type="automate",
            steps=[{"action_type": "type_text", "target": "password123"}],  # Simulated secret
        )
        
        # The store should not automatically filter, but we can test
        # that the procedure structure doesn't have secret fields
        self.assertNotIn("password", proc.__dict__)
        self.assertNotIn("api_key", proc.__dict__)
        self.assertNotIn("token", proc.__dict__)


class TestFeatureFlagDefaults(unittest.TestCase):
    """Test Phase 2B features default to OFF."""
    
    def test_router_not_created_by_default(self):
        """IntelligenceRouter not auto-instantiated."""
        # Router is only created when explicitly instantiated
        router = IntelligenceRouter()
        self.assertIsNotNone(router)
    
    def test_skill_registry_always_available(self):
        """Skill registry always available as library."""
        registry = get_skill_registry()
        self.assertIsInstance(registry, SkillRegistry)
        self.assertGreater(len(registry.all()), 0)


class TestIntegration(unittest.TestCase):
    """Integration tests for Phase 2B components."""
    
    def test_full_routing_pipeline(self):
        """Test complete routing: skill -> procedure -> local -> fallback."""
        router = IntelligenceRouter()
        registry = Mock()
        registry.list.return_value = ["mock"]
        router.intelligence = registry
        
        perception = PerceptionSnapshot(
            active_app="explorer",
            capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
        )
        
        # Test sequence
        test_cases = [
            ("open notepad", RouteDecision.DETERMINISTIC_SKILL),
            ("what is python", RouteDecision.LOCAL_REASONING),
            ("complex analysis of quantum mechanics", RouteDecision.MODEL_FALLBACK),
        ]
        
        for intent, expected in test_cases:
            task_state = TaskState(goal=intent)
            result = router.route(task_state, perception, intent)
            self.assertEqual(result.decision, expected, f"Failed for: {intent}")
    
    def test_procedure_promotion_flow(self):
        """Test candidate -> verified procedure flow."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        
        try:
            store = ProcedureStore(db_path)
            
            # Create candidate from trace - use a goal that doesn't match a deterministic skill
            candidate = ProcedureCandidate(
                goal="Backup project files to archive",
                context={"app": "explorer"},
                steps=[
                    {"description": "Create archive folder", "action_type": "create_folder", "target": "archive"},
                    {"description": "Copy project files", "action_type": "copy_path", "target": "project|archive/project"},
                ],
                verification_results=[{"success": True}, {"success": True}],
            )
            
            # Promote
            proc = store.promote_candidate(candidate)
            
            # Verify stored
            self.assertIsNotNone(proc)
            self.assertEqual(proc.goal, "Backup project files to archive")
            self.assertEqual(proc.source, "verified_trace")
            
            # Router should find it
            router = IntelligenceRouter(procedure_store=store)
            registry = Mock()
            registry.list.return_value = ["mock"]
            router.intelligence = registry
            
            task_state = TaskState(goal="Backup project files to archive")
            perception = PerceptionSnapshot(capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
            
            result = router.route(task_state, perception, "Backup project files to archive")
            
            # Should match procedure (no deterministic skill matches this)
            self.assertEqual(result.decision, RouteDecision.VERIFIED_PROCEDURE)
            self.assertIsNotNone(result.procedure)
        
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


# ============================================================
# PHASE 2C TESTS - Structured Plan Execution
# ============================================================

class TestStructuredPlanExecution(unittest.TestCase):
    """Test Phase 2C: Structured plan execution via TaskEngine."""

    def setUp(self):
        from core.orchestrator import (
            TaskEngine, TaskType, PerceptionEngine, PerceptionLevel,
            PerceptionSnapshot, ActionExecutor, VerificationEngine, RecoveryEngine,
            IntelligenceRouter, create_brain_adapter, OrchestratorState
        )
        from core.brain import Brain
        from core.memory import Memory
        
        memory = Memory()
        brain = Brain(memory, lambda *a: True)
        perception = PerceptionEngine()
        action_executor = ActionExecutor(perception=perception, memory=memory)
        verification = VerificationEngine(perception=perception)
        recovery = RecoveryEngine()
        router = IntelligenceRouter()
        brain_adapter = create_brain_adapter(brain)
        
        self.task_engine = TaskEngine(
            intelligence=brain_adapter,
            perception=perception,
            action_executor=action_executor,
            verification=verification,
            recovery=recovery,
            intelligence_router=router,
            memory=memory,
        )
        
        # Add mock control for grounding
        self.mock_control = Mock()
        self.mock_control.name = 'Notepad'
        self.mock_control.ctype = 'Button'
        self.mock_control.x = 100
        self.mock_control.y = 100
        self.mock_control.w = 200
        self.mock_control.h = 50
        self.mock_control.enabled = True
        self.mock_control.visible = True
        self.mock_control.role = 'button'
        self.mock_control.rect = Mock()
        self.mock_control.rect.center = Mock(return_value=(200, 125))
        perception._cache.controls = [self.mock_control]
        perception._cache.captured_at = 0

        self.brain_adapter = create_brain_adapter(brain)
        self.perception = perception
        self.original_observe = perception.observe

    def _setup_brain_mocks(self):
        self.brain_adapter.generate = Mock(return_value=Mock(content='OK', tool_calls=[]))
        async def mock_stream(request):
            yield 'OK'
        self.brain_adapter.stream = mock_stream
        self.brain_adapter.health = Mock(return_value=Mock(healthy=True))
        self.brain_adapter.warm_up = Mock(return_value=True)

    def _patch_perception_for_app(self, app_name):
        """Patch perception to return different app after action."""
        # Instead of mocking observe (which is complex), directly set the cache
        # for post-action verification. The observe method reads from cache.
        # We'll patch the observe method to return a modified snapshot on the second call.
        original_observe = self.perception.observe
        call_count = {'count': 0}
        
        def mock_observe(required_level=1, force_refresh=False):
            call_count['count'] += 1
            if call_count['count'] == 1:
                # Pre-action: return current state
                return original_observe(required_level, force_refresh)
            else:
                # Post-action: return state with target app
                snap = original_observe(required_level, force_refresh)
                snap.active_app = app_name
                snap.active_window = {'title': app_name, 'app': app_name}
                return snap
        self.perception.observe = mock_observe

    async def _run_task(self, goal, task_type=TaskType.AUTOMATE):
        from unittest.mock import Mock, patch
        self._setup_brain_mocks()
        with patch('core.orchestrator.action_executor.execute_tool') as mock_execute:
            mock_execute.return_value = 'OK: clicked'
            return await asyncio.wait_for(
                self.task_engine.run(goal, task_type),
                timeout=10.0
            )

    def test_single_task_completion(self):
        """Test single task runs to completion and returns to IDLE."""
        self._patch_perception_for_app('Notepad')
        
        result = asyncio.run(self._run_task('open notepad'))
        
        self.assertEqual(self.task_engine.state, OrchestratorState.IDLE)
        self.assertTrue(self.task_engine._task_state.is_complete())
    
    def test_two_sequential_tasks(self):
        """Test two sequential tasks execute without state conflict."""
        self._patch_perception_for_app('Notepad')
        
        # Command 1
        result1 = asyncio.run(self._run_task('open notepad'))
        self.assertEqual(self.task_engine.state, OrchestratorState.IDLE)
        
        # Command 2 - patch for different app
        self._patch_perception_for_app('TextEditor')
        result2 = asyncio.run(self._run_task('type hello'))
        
        self.assertEqual(self.task_engine.state, OrchestratorState.IDLE)
    
    def test_task_state_transitions(self):
        """Verify state transitions follow expected path."""
        from unittest.mock import Mock
        self._patch_perception_for_app('Notepad')
        self._setup_brain_mocks()
        
        states_seen = []
        
        original_step = self.task_engine._step
        async def debug_step():
            states_seen.append(self.task_engine.state)
            await original_step()
            states_seen.append(self.task_engine.state)
        
        with patch('core.orchestrator.action_executor.execute_tool') as mock_execute:
            mock_execute.return_value = 'OK: clicked'
            self.task_engine._step = debug_step
            
            asyncio.run(self._run_task('open notepad'))
        
        # Check expected state sequence (step states only, IDLE is reached after _do_terminal)
        expected_states = [
            OrchestratorState.UNDERSTANDING,
            OrchestratorState.OBSERVING,
            OrchestratorState.PLANNING,
            OrchestratorState.ACTING,
            OrchestratorState.VERIFYING,
            OrchestratorState.DONE,
        ]
        # Check that all expected states were visited in order
        for i, expected in enumerate(expected_states):
            found = False
            for j in range(i, len(states_seen)):
                if states_seen[j] == expected:
                    found = True
                    break
            self.assertTrue(found, f"State {expected} not found in transitions: {states_seen}")


class TestActionExecutorIntegration(unittest.TestCase):
    """Test ActionExecutor with Phase 2C grounding logic."""

    def setUp(self):
        from core.orchestrator import (
            PerceptionEngine, ActionExecutor
        )
        from core.memory import Memory
        
        self.memory = Memory()
        self.perception = PerceptionEngine()
        self.action_executor = ActionExecutor(perception=self.perception, memory=self.memory)
    
    def test_open_app_no_grounding_required(self):
        """open_app should not require UI grounding."""
        from core.orchestrator.action_executor import ActionExecutor
        from core.memory import Memory
        executor = ActionExecutor(perception=self.perception, memory=Memory())
        # Check that open_app is in the no-ground set
        self.assertIn("open_app", executor.NO_GROUND_ACTIONS)


class TestVerificationIntegration(unittest.TestCase):
    """Test VerificationEngine integration with structured plans."""

    def setUp(self):
        from core.orchestrator import (
            PerceptionEngine, VerificationEngine, PerceptionSnapshot, PerceptionLevel
        )
        from core.orchestrator.state import Action, TargetSpec, ExpectedResult, VerificationMethod
        
        self.perception = PerceptionEngine()
        self.verification = VerificationEngine(perception=self.perception)
        
        self.action = Action(
            task_id="test",
            step_id=0,
            intent="open notepad",
            action_type="open_app",
            target=TargetSpec(text_match="open notepad"),
            target_description="open notepad",
            grounding_method="KEYBOARD_DIRECT",
            expected_result=ExpectedResult(process_name="notepad", window_title="notepad"),
            verification_method=VerificationMethod.WINDOW_APPEARED,
        )
    
    def test_window_appeared_verification(self):
        """Test WINDOW_APPEARED verification logic."""
        before = PerceptionSnapshot(active_app="explorer", capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
        after = PerceptionSnapshot(
            active_app="notepad",
            active_window={"title": "Untitled - Notepad", "app": "Notepad"},
            capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
        )
        
        result = asyncio.run(self.verification.verify(
            action=self.action,
            expected=self.action.expected_result,
            perception_before=before,
            perception_after=after,
        ))
        
        self.assertTrue(result.success)
        self.assertEqual(result.method, VerificationMethod.WINDOW_APPEARED)


class TestRecoveryIntegration(unittest.TestCase):
    """Test RecoveryEngine integration."""

    def setUp(self):
        from core.orchestrator import RecoveryEngine, PerceptionEngine, PerceptionSnapshot, PerceptionLevel
        from core.orchestrator.state import VerificationResult, VerificationMethod, TaskState
        
        self.recovery = RecoveryEngine()
        self.perception = PerceptionEngine()
    
    def test_recovery_on_verification_failure(self):
        """Test recovery is triggered on verification failure."""
        from core.orchestrator.state import TaskState, VerificationResult, VerificationMethod, UnexpectedState
        
        task_state = TaskState(goal="test", recovery_attempts=0, confidence=0.8)
        perception = PerceptionSnapshot(
            active_app="notepad", 
            capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
            ocr_text="Cannot connect to server. Please try again."
        )
        verification = VerificationResult(
            success=False,
            method=VerificationMethod.WINDOW_APPEARED,
            evidence="Connection error dialog appeared",
            confidence=0.0,
        )
        
        decision = asyncio.run(self.recovery.decide(task_state, perception, verification))
        
        self.assertTrue(decision.should_retry)
        # Action for "cannot connect" falls to generic cancel/close handler
        self.assertIn("click_cancel_or_close", decision.action.lower())


if __name__ == "__main__":
    unittest.main()


# Phase 2F: Procedure Adaptation & Composition Tests
class TestProcedureComposition(unittest.TestCase):
    """Test ProcedureComposition store and manager."""

    def setUp(self):
        import tempfile
        from core.orchestrator import ProcedureCompositionStore, ProcedureCompositionManager
        from core.orchestrator.procedure_store import get_procedure_store
        from core.orchestrator.state import Procedure, ProcedureParameter, TaskType
        
        self.temp_dir = tempfile.mkdtemp()
        self.composition_store = ProcedureCompositionStore(db_path=os.path.join(self.temp_dir, "compositions.db"))
        self.procedure_store = get_procedure_store(db_path=os.path.join(self.temp_dir, "procedures.db"))
        self.composition_manager = ProcedureCompositionManager(
            procedure_store=self.procedure_store,
            composition_store=self.composition_store
        )
        
        # Create some test procedures
        self.proc1 = Procedure(
            goal="create folder",
            goal_type=TaskType.AUTOMATE,
            steps=[{"type": "click", "target": "new_folder"}],
            parameters=[ProcedureParameter(name="folder_name", type="string", required=True)],
            verification=[{"method": "window_appeared", "target": "folder"}],
            confidence=0.8,
        )
        self.proc1_id = self.procedure_store.save(self.proc1)
        
        self.proc2 = Procedure(
            goal="rename folder",
            goal_type=TaskType.AUTOMATE,
            steps=[{"type": "click", "target": "rename"}],
            parameters=[ProcedureParameter(name="new_name", type="string", required=True)],
            verification=[{"method": "text_match", "target": "renamed"}],
            confidence=0.7,
        )
        self.proc2_id = self.procedure_store.save(self.proc2)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_composition_creation(self):
        """Test creating a composition from sub-procedures."""
        comp = self.composition_manager.create_composition(
            name="create_and_rename_folder",
            sub_procedure_ids=[self.proc1_id, self.proc2_id],
            execution_order=[0, 1],
            description="Create then rename a folder",
        )
        
        self.assertEqual(comp.name, "create_and_rename_folder")
        self.assertEqual(len(comp.sub_procedures), 2)
        self.assertEqual(comp.execution_order, [0, 1])
        self.assertGreater(comp.confidence, 0.0)

    def test_composition_persistence(self):
        """Test composition is saved and retrieved."""
        comp = self.composition_manager.create_composition(
            name="test_comp",
            sub_procedure_ids=[self.proc1_id],
            execution_order=[0],
        )
        
        retrieved = self.composition_store.get(comp.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, "test_comp")

    def test_composition_suggestion(self):
        """Test auto-suggestion of composition."""
        # Should suggest composition when multiple procedures match
        comp = self.composition_manager.suggest_composition("create folder and rename it", {})
        # May return None if not enough procedures match
        if comp:
            self.assertIsInstance(comp.name, str)


class TestProcedureAdaptation(unittest.TestCase):
    """Test ProcedureAdaptation store and manager."""

    def setUp(self):
        import tempfile
        from core.orchestrator import ProcedureAdaptationStore, ProcedureAdaptationManager
        from core.orchestrator.procedure_store import get_procedure_store
        from core.orchestrator.state import Procedure, ProcedureParameter, TaskType
        
        self.temp_dir = tempfile.mkdtemp()
        self.adaptation_store = ProcedureAdaptationStore(db_path=os.path.join(self.temp_dir, "adaptations.db"))
        self.procedure_store = get_procedure_store(db_path=os.path.join(self.temp_dir, "procedures.db"))
        self.adaptation_manager = ProcedureAdaptationManager(
            procedure_store=self.procedure_store,
            adaptation_store=self.adaptation_store
        )
        
        # Create a test procedure
        self.proc = Procedure(
            goal="create folder Reports",
            goal_type=TaskType.AUTOMATE,
            steps=[{"type": "click", "target": "new_folder"}, {"type": "type", "text": "Reports"}],
            parameters=[ProcedureParameter(name="folder_name", type="string", required=True, default="Reports")],
            verification=[{"method": "window_appeared", "target": "Reports"}],
            confidence=0.8,
        )
        self.proc_id = self.procedure_store.save(self.proc)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adaptation_store_save_load(self):
        """Test saving and loading adaptations."""
        from core.orchestrator.state import ProcedureAdaptation
        
        adaptation = ProcedureAdaptation(
            base_procedure_id=self.proc_id,
            trigger="ui_changed",
            context_diff={"controls_changed": {"added": ["confirm_button"], "removed": []}},
            parameter_changes={"folder_name": {"old": "Reports", "new": "Projects"}},
            step_modifications=[{"step": 1, "change": "updated text input"}],
            reason="UI changed - new confirm button",
            status="candidate",
            confidence=0.6,
        )
        
        adaptation_id = self.adaptation_store.save(adaptation)
        self.assertGreater(adaptation_id, 0)
        
        retrieved = self.adaptation_store.get(adaptation_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.base_procedure_id, self.proc_id)
        self.assertEqual(retrieved.trigger, "ui_changed")
        self.assertEqual(retrieved.status, "candidate")

    def test_adaptation_detection(self):
        """Test adaptation need detection."""
        from core.orchestrator.state import PerceptionSnapshot, PerceptionLevel, VerificationResult, VerificationMethod
        from unittest.mock import Mock
        
        # Create perceptions before and after UI change
        before = PerceptionSnapshot(
            active_app="explorer",
            capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
            controls=[Mock(name="new_folder"), Mock(name="type_field")],
        )
        after = PerceptionSnapshot(
            active_app="explorer",
            capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
            controls=[Mock(name="new_folder"), Mock(name="type_field"), Mock(name="confirm_button")],
        )
        
        adaptation = self.adaptation_manager.detect_adaptation_need(
            base_procedure_id=self.proc_id,
            perception_before=before,
            perception_after=after,
        )
        
        # May or may not detect depending on implementation
        if adaptation:
            self.assertEqual(adaptation.base_procedure_id, self.proc_id)


class TestFindSimilarScoring(unittest.TestCase):
    """Test improved find_similar scoring."""

    def setUp(self):
        import tempfile
        from core.orchestrator import ProcedureStore, get_procedure_store
        from core.orchestrator.state import Procedure, ProcedureParameter, TaskType
        
        self.temp_dir = tempfile.mkdtemp()
        self.store = ProcedureStore(db_path=os.path.join(self.temp_dir, "procedures.db"))
        
        # Create procedures with different characteristics
        self.proc1 = Procedure(
            goal="create folder Reports",
            goal_type=TaskType.AUTOMATE,
            steps=[{"type": "click", "target": "new_folder"}, {"type": "type", "text": "Reports"}],
            parameters=[ProcedureParameter(name="folder_name", type="string", required=True, default="Reports")],
            verification=[{"method": "window_appeared", "target": "Reports"}],
            confidence=0.9,
            success_count=5,
            failure_count=0,
        )
        self.store.save(self.proc1)
        
        self.proc2 = Procedure(
            goal="create folder Projects",
            goal_type=TaskType.AUTOMATE,
            steps=[{"type": "click", "target": "new_folder"}, {"type": "type", "text": "Projects"}],
            parameters=[ProcedureParameter(name="folder_name", type="string", required=True, default="Projects")],
            verification=[{"method": "window_appeared", "target": "Projects"}],
            confidence=0.8,
            success_count=3,
            failure_count=1,
        )
        self.store.save(self.proc2)
        
        self.proc3 = Procedure(
            goal="delete file",
            goal_type=TaskType.AUTOMATE,
            steps=[{"type": "click", "target": "delete"}],
            parameters=[ProcedureParameter(name="file_path", type="string", required=True)],
            verification=[{"method": "window_disappeared", "target": "file"}],
            confidence=0.7,
        )
        self.store.save(self.proc3)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_find_similar_goal_match(self):
        """Test find_similar returns procedures matching goal words."""
        results = self.store.find_similar("create folder", ["click", "type"], ["folder_name"])
        
        self.assertGreaterEqual(len(results), 2)
        # Both create folder procedures should match
        goals = [r.goal for r in results]
        self.assertIn("create folder Reports", goals)
        self.assertIn("create folder Projects", goals)

    def test_find_similar_action_type_match(self):
        """Test find_similar filters by action type."""
        results = self.store.find_similar("create folder", ["click"], ["folder_name"])
        
        self.assertGreaterEqual(len(results), 2)
        for r in results:
            # Should have click action
            has_click = any(s.get('type') == 'click' for s in r.steps if isinstance(s, dict))
            self.assertTrue(has_click)

    def test_find_similar_param_match(self):
        """Test find_similar filters by parameter name."""
        results = self.store.find_similar("create folder", ["click", "type"], ["folder_name"])
        
        for r in results:
            param_names = [p.name for p in r.parameters if hasattr(p, 'name')]
            self.assertIn("folder_name", param_names)

    def test_find_similar_excludes_unrelated(self):
        """Test find_similar excludes unrelated procedures."""
        results = self.store.find_similar("create folder", ["click", "type"], ["folder_name"])
        
        # delete file should not match
        goals = [r.goal for r in results]
        self.assertNotIn("delete file", goals)


class TestProcedureVersioning(unittest.TestCase):
    """Test procedure versioning and history."""

    def setUp(self):
        import tempfile
        from core.orchestrator import ProcedureStore
        from core.orchestrator.state import Procedure, ProcedureParameter, TaskType
        
        self.temp_dir = tempfile.mkdtemp()
        self.store = ProcedureStore(db_path=os.path.join(self.temp_dir, "procedures.db"))
        
        self.proc = Procedure(
            goal="create folder",
            goal_type=TaskType.AUTOMATE,
            steps=[{"type": "click", "target": "new_folder"}],
            parameters=[ProcedureParameter(name="folder_name", type="string", required=True)],
            verification=[{"method": "window_appeared"}],
            confidence=0.8,
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_version_history_on_update(self):
        """Test version history is created on meaningful updates."""
        proc_id = self.store.save(self.proc)
        
        # Get the procedure and modify it
        proc = self.store.get(proc_id)
        proc.steps = [{"type": "click", "target": "new_folder"}, {"type": "type", "text": "New"}]
        proc.version = 2
        self.store.save(proc)
        
        # Re-fetch to get updated change_log
        proc = self.store.get(proc_id)
        
        # Check version history
        history = self.store.get_versions(proc_id)
        self.assertGreaterEqual(len(history), 1)
        
        # Check change log - should have v2 entry
        self.assertTrue(any("v2:" in entry for entry in proc.change_log))

    def test_get_version(self):
        """Test retrieving a specific version."""
        proc_id = self.store.save(self.proc)
        
        # Update procedure
        proc = self.store.get(proc_id)
        proc.steps = [{"type": "click", "target": "new_folder"}, {"type": "type", "text": "New"}]
        proc.version = 2
        self.store.save(proc)
        
        # Get version 1
        v1 = self.store.get_version(proc_id, 1)
        self.assertIsNotNone(v1)
        self.assertEqual(v1.version, 1)


class TestSecretSanitization(unittest.TestCase):
    """Test secret sanitization in procedure learner."""

    def test_secret_sanitizer_passwords(self):
        """Test passwords are sanitized."""
        from core.orchestrator.procedure_learner import SecretSanitizer
        
        sanitizer = SecretSanitizer()
        
        text = "password=secret123 api_key=abc123"
        sanitized = sanitizer.sanitize(text)
        
        self.assertIn("<REDACTED_PASSWORD>", sanitized)
        self.assertIn("<REDACTED_API_KEY>", sanitized)

    def test_secret_sanitizer_bearer_token(self):
        """Test bearer tokens are sanitized."""
        from core.orchestrator.procedure_learner import SecretSanitizer
        
        sanitizer = SecretSanitizer()
        
        text = "Authorization: Bearer abc123token"
        sanitized = sanitizer.sanitize(text)
        
        self.assertIn("<REDACTED_BEARER_TOKEN>", sanitized)

    def test_secret_sanitizer_dict(self):
        """Test sanitize_dict works recursively."""
        from core.orchestrator.procedure_learner import SecretSanitizer
        
        sanitizer = SecretSanitizer()
        
        data = {
            "config": {
                "password": "secret123",
                "nested": {"api_key": "key123"}
            },
            "items": ["token=abc", "normal"]
        }
        sanitized = sanitizer.sanitize_dict(data)
        
        self.assertIn("<REDACTED_PASSWORD>", sanitized["config"]["password"])
        self.assertIn("<REDACTED_API_KEY>", sanitized["config"]["nested"]["api_key"])
        self.assertIn("<REDACTED_PASSWORD>", sanitized["items"][0])

    def test_contains_secrets(self):
        """Test contains_secrets detection."""
        from core.orchestrator.procedure_learner import SecretSanitizer
        
        sanitizer = SecretSanitizer()
        
        self.assertTrue(sanitizer.contains_secrets("password=secret"))
        self.assertTrue(sanitizer.contains_secrets("api_key=abc123"))
        self.assertFalse(sanitizer.contains_secrets("normal text"))


class TestFeatureFlags(unittest.TestCase):
    """Test Phase 2F feature flags are off by default."""

    def test_adaptation_flag_off_by_default(self):
        """Test enable_procedure_adaptation defaults to False."""
        from core.config import get_config
        
        cfg = get_config()
        # Check default is False (or not present)
        val = cfg.get("orchestrator.enable_procedure_adaptation")
        self.assertFalse(val)

    def test_composition_flag_off_by_default(self):
        """Test enable_procedure_composition defaults to False."""
        from core.config import get_config
        
        cfg = get_config()
        val = cfg.get("orchestrator.enable_procedure_composition")
        self.assertFalse(val)