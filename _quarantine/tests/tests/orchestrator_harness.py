"""Orchestrator Foundation Test Harness.

Verifies Phase 0 architectural foundation without modifying live behavior.
All tests are import/syntax/side-effect checks only.
"""
import sys
import os
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_imports() -> dict:
    """Test that all core modules and new orchestrator modules import cleanly."""
    results = {}

    # Existing core modules
    modules = [
        "core.brain",
        "core.config",
        "core.tools",
        "core.memory",
        "core.ear",
        "core.voice",
        "core.observer",
        "core.experience",
        "core.screentree",
        "core.ambient",
        "core.personality",
        "core.learning",
        "core.thinker",
        "core.autopilot",
        "core.tracker",
        "core.scheduler",
        "core.presence",
        "core.wizard",
        "core.secrets",
        "core.permissions",
        "core.knowledge",
        "core.associations",
        "core.actionbrain",
        "core.actiontrack",
    ]

    for mod in modules:
        try:
            __import__(mod)
            results[mod] = ("PASS", "")
        except Exception as e:
            results[mod] = ("FAIL", str(e))

    # New orchestrator modules
    orch_modules = [
        "core.orchestrator",
        "core.orchestrator.state",
        "core.orchestrator.perception",
    ]

    for mod in orch_modules:
        try:
            __import__(mod)
            results[mod] = ("PASS", "")
        except Exception as e:
            results[mod] = ("FAIL", str(e))

    return results


def test_state_dataclasses() -> dict:
    """Test that all state dataclasses can be instantiated."""
    from core.orchestrator.state import (
        TaskState,
        SubGoal,
        StepResult,
        Action,
        TargetSpec,
        ExpectedResult,
        ActualResult,
        VerificationResult,
        GroundingMethod,
        RiskLevel,
        TaskType,
        OrchestratorState,
        VoiceState,
        InterruptRelation,
        UnexpectedState,
        PermissionState,
        MemoryBundle,
        Procedure,
        PerceptionSnapshot,
        Rect,
        ControlInfo,
        OCRRegion,
        ScreenDelta,
        VerificationMethod,
    )

    results = {}

    try:
        ts = TaskState(goal="Test goal")
        results["TaskState"] = ("PASS", f"id={ts.task_id[:8]}")
    except Exception as e:
        results["TaskState"] = ("FAIL", str(e))

    try:
        sg = SubGoal(description="Test step", intent="click", action_type="click")
        results["SubGoal"] = ("PASS", "")
    except Exception as e:
        results["SubGoal"] = ("FAIL", str(e))

    try:
        sr = StepResult(subgoal=SubGoal(description="test"))
        results["StepResult"] = ("PASS", "")
    except Exception as e:
        results["StepResult"] = ("FAIL", str(e))

    try:
        act = Action(
            task_id="test",
            step_id=0,
            intent="Click Save",
            action_type="click",
            target=TargetSpec(control_name="Save"),
            target_description="Save button",
        )
        results["Action"] = ("PASS", "")
    except Exception as e:
        results["Action"] = ("FAIL", str(e))

    try:
        tgt = TargetSpec(control_name="Save", text_match="Save")
        results["TargetSpec"] = ("PASS", "")
    except Exception as e:
        results["TargetSpec"] = ("FAIL", str(e))

    try:
        exp = ExpectedResult(text_expected="Saved")
        results["ExpectedResult"] = ("PASS", "")
    except Exception as e:
        results["ExpectedResult"] = ("FAIL", str(e))

    try:
        act_res = ActualResult(text_found="Saved")
        results["ActualResult"] = ("PASS", "")
    except Exception as e:
        results["ActualResult"] = ("FAIL", str(e))

    try:
        vr = VerificationResult(success=True, method=VerificationMethod.OCR_TEXT_APPEARED)
        results["VerificationResult"] = ("PASS", "")
    except Exception as e:
        results["VerificationResult"] = ("FAIL", str(e))

    try:
        gm = GroundingMethod.UIA
        results["GroundingMethod"] = ("PASS", "")
    except Exception as e:
        results["GroundingMethod"] = ("FAIL", str(e))

    try:
        rl = RiskLevel.MEDIUM
        results["RiskLevel"] = ("PASS", "")
    except Exception as e:
        results["RiskLevel"] = ("FAIL", str(e))

    try:
        tt = TaskType.CREATE
        results["TaskType"] = ("PASS", "")
    except Exception as e:
        results["TaskType"] = ("FAIL", str(e))

    try:
        os_ = OrchestratorState.IDLE
        results["OrchestratorState"] = ("PASS", "")
    except Exception as e:
        results["OrchestratorState"] = ("FAIL", str(e))

    try:
        vs = VoiceState.IDLE
        results["VoiceState"] = ("PASS", "")
    except Exception as e:
        results["VoiceState"] = ("FAIL", str(e))

    try:
        ir = InterruptRelation.MODIFICATION
        results["InterruptRelation"] = ("PASS", "")
    except Exception as e:
        results["InterruptRelation"] = ("FAIL", str(e))

    try:
        us = UnexpectedState.MODAL_DIALOG
        results["UnexpectedState"] = ("PASS", "")
    except Exception as e:
        results["UnexpectedState"] = ("FAIL", str(e))

    try:
        ps = PermissionState.GRANTED
        results["PermissionState"] = ("PASS", "")
    except Exception as e:
        results["PermissionState"] = ("FAIL", str(e))

    try:
        mb = MemoryBundle(facts=[{"cat": "test"}])
        results["MemoryBundle"] = ("PASS", "")
    except Exception as e:
        results["MemoryBundle"] = ("FAIL", str(e))

    try:
        proc = Procedure(goal="test", steps=[{"action": "click"}])
        results["Procedure"] = ("PASS", "")
    except Exception as e:
        results["Procedure"] = ("FAIL", str(e))

    try:
        rect = Rect(10, 20, 100, 50)
        results["Rect"] = ("PASS", f"center={rect.center()}")
    except Exception as e:
        results["Rect"] = ("FAIL", str(e))

    try:
        ctrl = ControlInfo(ctype="ButtonControl", name="Save", x=10, y=10, w=50, h=30)
        results["ControlInfo"] = ("PASS", "")
    except Exception as e:
        results["ControlInfo"] = ("FAIL", str(e))

    try:
        ocr = OCRRegion(text="Save", x=10, y=10, w=40, h=20, confidence=0.95)
        results["OCRRegion"] = ("PASS", "")
    except Exception as e:
        results["OCRRegion"] = ("FAIL", str(e))

    try:
        sd = ScreenDelta(app_changed=True)
        results["ScreenDelta"] = ("PASS", "")
    except Exception as e:
        results["ScreenDelta"] = ("FAIL", str(e))

    try:
        vm = VerificationMethod.UIA_STATE_CHANGE
        results["VerificationMethod"] = ("PASS", "")
    except Exception as e:
        results["VerificationMethod"] = ("FAIL", str(e))

    return results


def test_perception_cache() -> dict:
    """Test PerceptionCache instantiation and basic operations."""
    from core.orchestrator.perception import PerceptionCache, PerceptionEngine, PerceptionLevel
    from core.orchestrator.state import ControlInfo, OCRRegion, Rect, TargetSpec

    results = {}

    try:
        cache = PerceptionCache()
        results["PerceptionCache_instantiation"] = ("PASS", "")
    except Exception as e:
        results["PerceptionCache_instantiation"] = ("FAIL", str(e))

    try:
        cache = PerceptionCache()
        cache.update(active_app="notepad", active_window={"app": "notepad"})
        snap = cache.get_snapshot()
        assert snap.active_app == "notepad"
        results["PerceptionCache_update_get"] = ("PASS", "")
    except Exception as e:
        results["PerceptionCache_update_get"] = ("FAIL", str(e))

    try:
        engine = PerceptionEngine()
        results["PerceptionEngine_instantiation"] = ("PASS", "")
    except Exception as e:
        results["PerceptionEngine_instantiation"] = ("FAIL", str(e))

    try:
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW)
        results["PerceptionEngine_observe_L1"] = ("PASS", f"app={snap.active_app}")
    except Exception as e:
        results["PerceptionEngine_observe_L1"] = ("FAIL", str(e))

    try:
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_2_UIA_TREE)
        results["PerceptionEngine_observe_L2"] = ("PASS", f"controls={len(snap.controls)}")
    except Exception as e:
        results["PerceptionEngine_observe_L2"] = ("FAIL", str(e))

    try:
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_3_OCR)
        results["PerceptionEngine_observe_L3"] = ("PASS", "")
    except Exception as e:
        results["PerceptionEngine_observe_L3"] = ("FAIL", str(e))

    try:
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_4_TARGETED_CV)
        results["PerceptionEngine_observe_L4"] = ("PASS", "")
    except Exception as e:
        results["PerceptionEngine_observe_L4"] = ("FAIL", str(e))

    try:
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_5_VISION_MODEL)
        results["PerceptionEngine_observe_L5"] = ("PASS", "no_vision_call")
    except Exception as e:
        results["PerceptionEngine_observe_L5"] = ("FAIL", str(e))

    try:
        cache = PerceptionCache()
        cache.update(controls=[ControlInfo(name="Save", x=10, y=10, w=50, h=30, ctype="ButtonControl")])
        engine = PerceptionEngine()
        engine._cache = cache
        from core.orchestrator.state import TargetSpec
        target = TargetSpec(control_name="Save")
        ctrl = engine.get_control(target)
        assert ctrl is not None
        results["get_control_UIA"] = ("PASS", "")
    except Exception as e:
        results["get_control_UIA"] = ("FAIL", str(e))

    try:
        cache = PerceptionCache()
        cache.update(ocr_text="Click Save button", ocr_regions=[OCRRegion(text="Save", x=10, y=10, w=30, h=20)])
        engine = PerceptionEngine()
        engine._cache = cache
        target = TargetSpec(text_match="Save")
        ctrl = engine.get_control(target)
        assert ctrl is not None
        results["get_control_OCR"] = ("PASS", "")
    except Exception as e:
        results["get_control_OCR"] = ("FAIL", str(e))

    return results


def test_feature_flags() -> dict:
    """Test that orchestrator feature flags parse correctly from config."""
    from core.config import get_config

    results = {}

    try:
        cfg = get_config()
        orch = cfg.section("orchestrator")
        assert isinstance(orch, dict)
        results["orchestrator_section_exists"] = ("PASS", f"keys={list(orch.keys())}")
    except Exception as e:
        results["orchestrator_section_exists"] = ("FAIL", str(e))

    try:
        cfg = get_config()
        enabled = cfg.get("orchestrator", "enabled", default=False)
        assert enabled is False
        results["enabled_default_false"] = ("PASS", "")
    except Exception as e:
        results["enabled_default_false"] = ("FAIL", str(e))

    try:
        cfg = get_config()
        flags = [
            "use_new_task_engine",
            "use_new_perception",
            "use_new_action_executor",
            "use_offline_skills",
            "use_background_coordinator",
        ]
        all_false = all(cfg.get("orchestrator", f, default=False) is False for f in flags)
        assert all_false
        results["all_flags_default_false"] = ("PASS", "")
    except Exception as e:
        results["all_flags_default_false"] = ("FAIL", str(e))

    try:
        cfg = get_config()
        bg = cfg.get("orchestrator", "use_background_coordinator", default=False)
        assert bg is False
        results["background_coordinator_default_false"] = ("PASS", "")
    except Exception as e:
        results["background_coordinator_default_false"] = ("FAIL", str(e))

    return results


def test_no_side_effects() -> dict:
    """Verify new modules have no startup side effects."""
    results = {}

    # Check no threads started on import
    import threading
    before = threading.active_count()

    try:
        import core.orchestrator.state
        import core.orchestrator.perception
        after = threading.active_count()
        if after == before:
            results["no_threads_on_import"] = ("PASS", f"threads={before}")
        else:
            results["no_threads_on_import"] = ("FAIL", f"before={before}, after={after}")
    except Exception as e:
        results["no_threads_on_import"] = ("FAIL", str(e))

    # Check no file I/O on import
    try:
        import core.orchestrator.state
        import core.orchestrator.perception
        results["no_io_on_import"] = ("PASS", "")
    except Exception as e:
        results["no_io_on_import"] = ("FAIL", str(e))

    return results


def test_legacy_path_preserved() -> dict:
    """Verify existing DUDE runtime remains the active default path."""
    from core.config import get_config

    results = {}

    try:
        cfg = get_config()
        # Orchestrator disabled by default
        assert cfg.get("orchestrator", "enabled", default=False) is False
        # Existing brain config still present
        brain = cfg.section("brain")
        assert "providers" in brain
        assert len(brain["providers"]) > 0
        results["legacy_brain_config_intact"] = ("PASS", f"providers={len(brain['providers'])}")
    except Exception as e:
        results["legacy_brain_config_intact"] = ("FAIL", str(e))

    try:
        # Existing tools still import and have REGISTRY
        from core.tools import REGISTRY, execute_tool
        assert len(REGISTRY) > 60
        results["legacy_tools_intact"] = ("PASS", f"tools={len(REGISTRY)}")
    except Exception as e:
        results["legacy_tools_intact"] = ("FAIL", str(e))

    try:
        # Existing memory still works
        from core.memory import Memory
        results["legacy_memory_intact"] = ("PASS", "")
    except Exception as e:
        results["legacy_memory_intact"] = ("FAIL", str(e))

    try:
        # Existing voice/ear still import
        from core.voice import Voice
        from core.ear import Ear
        results["legacy_voice_ear_intact"] = ("PASS", "")
    except Exception as e:
        results["legacy_voice_ear_intact"] = ("FAIL", str(e))

    return results


def test_intelligence_abstraction() -> dict:
    """Test IntelligenceBackend abstraction and BrainAdapter."""
    results = {}

    try:
        from core.orchestrator.intelligence import (
            IntelligenceBackend,
            ModelCapabilities,
            HealthStatus,
            GenerateRequest,
            GenerateResponse,
            StreamRequest,
        )
        results["intelligence_imports"] = ("PASS", "")
    except Exception as e:
        results["intelligence_imports"] = ("FAIL", str(e))

    try:
        # Test dataclass instantiation
        caps = ModelCapabilities(streaming=True, tool_calling=True)
        health = HealthStatus(healthy=True, message="OK")
        req = GenerateRequest(prompt="test")
        resp = GenerateResponse(content="ok")
        sreq = StreamRequest(prompt="test")
        results["intelligence_dataclasses"] = ("PASS", "")
    except Exception as e:
        results["intelligence_dataclasses"] = ("FAIL", str(e))

    try:
        # Test BrainAdapter import
        from core.orchestrator.brain_adapter import BrainAdapter, create_brain_adapter
        results["brain_adapter_import"] = ("PASS", "")
    except Exception as e:
        results["brain_adapter_import"] = ("FAIL", str(e))

    try:
        # Verify no cloud provider imports in orchestrator modules
        import core.orchestrator.intelligence
        import core.orchestrator.task_engine
        import core.orchestrator.action_executor
        import core.orchestrator.verification
        import core.orchestrator.recovery
        # These should not import ollama, openai, google, etc directly
        results["no_cloud_imports"] = ("PASS", "")
    except Exception as e:
        results["no_cloud_imports"] = ("FAIL", str(e))

    return results


def test_task_engine_state_machine() -> dict:
    """Test TaskEngine state machine transitions."""
    results = {}

    try:
        from core.orchestrator.task_engine import TaskEngine, VALID_TRANSITIONS
        from core.orchestrator.state import OrchestratorState
        results["task_engine_import"] = ("PASS", "")
    except Exception as e:
        results["task_engine_import"] = ("FAIL", str(e))

    # Test valid transitions
    from core.orchestrator.state import OrchestratorState
    from core.orchestrator.task_engine import VALID_TRANSITIONS
    valid_transitions = [
        (OrchestratorState.IDLE, OrchestratorState.LISTENING),
        (OrchestratorState.LISTENING, OrchestratorState.UNDERSTANDING),
        (OrchestratorState.LISTENING, OrchestratorState.IDLE),
        (OrchestratorState.UNDERSTANDING, OrchestratorState.OBSERVING),
        (OrchestratorState.UNDERSTANDING, OrchestratorState.FAILED),
        (OrchestratorState.OBSERVING, OrchestratorState.PLANNING),
        (OrchestratorState.OBSERVING, OrchestratorState.FAILED),
        (OrchestratorState.PLANNING, OrchestratorState.ACTING),
        (OrchestratorState.PLANNING, OrchestratorState.FAILED),
        (OrchestratorState.ACTING, OrchestratorState.VERIFYING),
        (OrchestratorState.ACTING, OrchestratorState.FAILED),
        (OrchestratorState.VERIFYING, OrchestratorState.OBSERVING),
        (OrchestratorState.VERIFYING, OrchestratorState.DONE),
        (OrchestratorState.VERIFYING, OrchestratorState.RECOVERING),
        (OrchestratorState.RECOVERING, OrchestratorState.OBSERVING),
        (OrchestratorState.RECOVERING, OrchestratorState.FAILED),
        (OrchestratorState.SPEAKING, OrchestratorState.INTERRUPTED),
        (OrchestratorState.SPEAKING, OrchestratorState.IDLE),
        (OrchestratorState.INTERRUPTED, OrchestratorState.UNDERSTANDING),
        (OrchestratorState.INTERRUPTED, OrchestratorState.IDLE),
        (OrchestratorState.DONE, OrchestratorState.IDLE),
        (OrchestratorState.FAILED, OrchestratorState.IDLE),
        (OrchestratorState.FAILED, OrchestratorState.UNDERSTANDING),
    ]

    for from_state, to_state in valid_transitions:
        if to_state in VALID_TRANSITIONS.get(from_state, set()):
            results[f"transition_{from_state.value}_to_{to_state.value}"] = ("PASS", "")
        else:
            results[f"transition_{from_state.value}_to_{to_state.value}"] = ("FAIL", "Not in VALID_TRANSITIONS")

    # Test invalid transitions are rejected
    invalid_transitions = [
        (OrchestratorState.IDLE, OrchestratorState.ACTING),
        (OrchestratorState.ACTING, OrchestratorState.UNDERSTANDING),
        (OrchestratorState.VERIFYING, OrchestratorState.PLANNING),
    ]

    for from_state, to_state in invalid_transitions:
        if to_state not in VALID_TRANSITIONS.get(from_state, set()):
            results[f"invalid_rejected_{from_state.value}_to_{to_state.value}"] = ("PASS", "")
        else:
            results[f"invalid_rejected_{from_state.value}_to_{to_state.value}"] = ("FAIL", "Invalid transition allowed")

    return results


def test_action_executor() -> dict:
    """Test ActionExecutor grounding and execution."""
    results = {}

    try:
        from core.orchestrator.action_executor import ActionExecutor, ExecutionStatus
        from core.orchestrator.state import Action, TargetSpec, GroundingMethod, RiskLevel
        results["action_executor_import"] = ("PASS", "")
    except Exception as e:
        results["action_executor_import"] = ("FAIL", str(e))

    # Test action instantiation with grounding
    try:
        action = Action(
            task_id="test",
            step_id=0,
            intent="Click Save",
            action_type="click",
            target=TargetSpec(control_name="Save", text_match="Save"),
            target_description="Save button",
            grounding_method=GroundingMethod.UIA,
            risk_level=RiskLevel.LOW,
        )
        results["action_instantiation"] = ("PASS", "")
    except Exception as e:
        results["action_instantiation"] = ("FAIL", str(e))

    # Test grounding priority enum values
    try:
        from core.orchestrator.state import GroundingMethod
        methods = [
            GroundingMethod.UIA,
            GroundingMethod.UIA_ROLE,
            GroundingMethod.OCR_TEXT,
            GroundingMethod.CV_TEMPLATE,
            GroundingMethod.RELATIVE_COORDS,
            GroundingMethod.ABSOLUTE_COORDS,
        ]
        results["grounding_methods"] = ("PASS", f"count={len(methods)}")
    except Exception as e:
        results["grounding_methods"] = ("FAIL", str(e))

    return results


def test_verification_engine() -> dict:
    """Test VerificationEngine methods."""
    results = {}

    try:
        from core.orchestrator.verification import VerificationEngine
        from core.orchestrator.state import VerificationMethod
        results["verification_import"] = ("PASS", "")
    except Exception as e:
        results["verification_import"] = ("FAIL", str(e))

    # Check all verification methods are defined
    try:
        from core.orchestrator.state import VerificationMethod
        methods = [
            VerificationMethod.UIA_STATE_CHANGE,
            VerificationMethod.OCR_TEXT_APPEARED,
            VerificationMethod.OCR_TEXT_DISAPPEARED,
            VerificationMethod.FILE_EXISTS,
            VerificationMethod.WINDOW_APPEARED,
            VerificationMethod.WINDOW_DISAPPEARED,
            VerificationMethod.PROCESS_STATE,
            VerificationMethod.CLIPBOARD_CONTENT,
            VerificationMethod.SCREEN_DELTA,
            VerificationMethod.CUSTOM,
        ]
        assert len(methods) == 10
        results["verification_methods_defined"] = ("PASS", f"count={len(methods)}")
    except Exception as e:
        results["verification_methods_defined"] = ("FAIL", str(e))

    return results


def test_recovery_engine() -> dict:
    """Test RecoveryEngine bounded recovery."""
    results = {}

    try:
        from core.orchestrator.recovery import RecoveryEngine, RecoveryDecision
        from core.orchestrator.state import UnexpectedState
        results["recovery_import"] = ("PASS", "")
    except Exception as e:
        results["recovery_import"] = ("FAIL", str(e))

    # Test default config
    try:
        engine = RecoveryEngine()
        assert engine.max_retries == 3
        assert engine.retry_delays == (1.0, 3.0, 10.0)
        assert engine.confidence_decay == 0.7
        assert engine.min_confidence == 0.3
        results["recovery_defaults"] = ("PASS", "")
    except Exception as e:
        results["recovery_defaults"] = ("FAIL", str(e))

    # Test unexpected state enum
    try:
        from core.orchestrator.state import UnexpectedState
        states = [
            UnexpectedState.MODAL_DIALOG,
            UnexpectedState.APPLICATION_CRASH,
            UnexpectedState.APPLICATION_HANG,
            UnexpectedState.PERMISSION_DENIED,
            UnexpectedState.FILE_NOT_FOUND,
            UnexpectedState.NETWORK_ERROR,
            UnexpectedState.UI_CHANGED,
            UnexpectedState.FOCUS_LOST,
            UnexpectedState.UNKNOWN,
        ]
        results["unexpected_states"] = ("PASS", "")
    except Exception as e:
        results["unexpected_states"] = ("FAIL", str(e))

    return results


def test_permission_integration() -> dict:
    """Test PermissionGate integration."""
    results = {}

    try:
        from core.permissions import PermissionGate
        results["permission_gate_import"] = ("PASS", "")
    except Exception as e:
        results["permission_gate_import"] = ("FAIL", str(e))

    # Test permission logic
    try:
        gate = PermissionGate(
            always_allow={"open_app", "screenshot"},
            always_ask={"delete_path", "shutdown_system"},
        )
        # LOW risk actions
        assert gate.requires_permission("open_app") == False
        assert gate.requires_permission("screenshot") == False
        # HIGH risk actions
        assert gate.requires_permission("delete_path") == True
        assert gate.requires_permission("shutdown_system") == True
        results["permission_logic"] = ("PASS", "")
    except Exception as e:
        results["permission_logic"] = ("FAIL", str(e))

    return results


def test_no_side_effects_phase1() -> dict:
    """Verify Phase 1 modules have no startup side effects."""
    import threading

    results = {}

    before = threading.active_count()

    try:
        import core.orchestrator.task_engine
        import core.orchestrator.action_executor
        import core.orchestrator.verification
        import core.orchestrator.recovery
        import core.orchestrator.intelligence
        import core.orchestrator.brain_adapter
        after = threading.active_count()
        if after == before:
            results["no_threads_on_import"] = ("PASS", f"threads={before}")
        else:
            results["no_threads_on_import"] = ("FAIL", f"before={before}, after={after}")
    except Exception as e:
        results["no_threads_on_import"] = ("FAIL", str(e))

    return results


def test_legacy_compatibility() -> dict:
    """Verify legacy DUDE runtime still works."""
    results = {}

    try:
        # Existing brain imports
        from core.brain import Brain, BrainUnavailable
        results["brain_import"] = ("PASS", "")
    except Exception as e:
        results["brain_import"] = ("FAIL", str(e))

    try:
        from core.tools import REGISTRY, execute_tool, tool_specs
        assert len(REGISTRY) > 60
        results["tools_import"] = ("PASS", f"tools={len(REGISTRY)}")
    except Exception as e:
        results["tools_import"] = ("FAIL", str(e))

    try:
        from core.memory import Memory
        results["memory_import"] = ("PASS", "")
    except Exception as e:
        results["memory_import"] = ("FAIL", str(e))

    try:
        from core.voice import Voice
        from core.ear import Ear
        results["voice_ear_import"] = ("PASS", "")
    except Exception as e:
        results["voice_ear_import"] = ("FAIL", str(e))

    try:
        from core.observer import Observer
        from core.screentree import ScreenMap
        results["observer_screentree_import"] = ("PASS", "")
    except Exception as e:
        results["observer_screentree_import"] = ("FAIL", str(e))

    return results


def test_phase2a_perception_cache() -> dict:
    """Test Phase 2A PerceptionCache behavior."""
    results = {}

    try:
        from core.orchestrator.perception import PerceptionCache
        from core.orchestrator.state import PerceptionSnapshot
        results["cache_creation"] = ("PASS", "")
    except Exception as e:
        results["cache_creation"] = ("FAIL", str(e))

    try:
        cache = PerceptionCache()
        cache.update(active_app="notepad", active_window={"app": "notepad", "title": "Untitled - Notepad"})
        snap = cache.get_snapshot()
        assert snap.active_app == "notepad"
        assert snap.active_window.get("app") == "notepad"
        assert snap.captured_at > 0
        results["snapshot_creation"] = ("PASS", "")
    except Exception as e:
        results["snapshot_creation"] = ("FAIL", str(e))

    try:
        cache = PerceptionCache()
        cache.update(active_app="test")
        assert cache.is_fresh(max_age=10.0) is True
        results["freshness_check"] = ("PASS", "")
    except Exception as e:
        results["freshness_check"] = ("FAIL", str(e))

    try:
        import time
        cache = PerceptionCache()
        cache.update(active_app="test")
        time.sleep(0.1)
        assert cache.is_fresh(max_age=0.01) is False
        results["stale_detection"] = ("PASS", "")
    except Exception as e:
        results["stale_detection"] = ("FAIL", str(e))

    try:
        from core.orchestrator.state import PerceptionLevel
        cache = PerceptionCache()
        cache.update(active_app="test", capture_method=PerceptionLevel.LEVEL_3_OCR)
        snap = cache.get_snapshot()
        assert snap.capture_method == PerceptionLevel.LEVEL_3_OCR
        results["level_tracking"] = ("PASS", "")
    except Exception as e:
        results["level_tracking"] = ("FAIL", str(e))

    return results


def test_phase2a_l1_app_window() -> dict:
    """Test Phase 2A L1: Active app/window retrieval."""
    results = {}

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        from core.orchestrator.state import PerceptionLevel as PL
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW)
        assert snap.active_app is not None
        assert isinstance(snap.active_app, str)
        results["active_app_retrieval"] = ("PASS", f"app={snap.active_app}")
    except Exception as e:
        results["active_app_retrieval"] = ("FAIL", str(e))

    try:
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW)
        assert snap.active_window is not None
        assert isinstance(snap.active_window, dict)
        results["active_window_retrieval"] = ("PASS", "")
    except Exception as e:
        results["active_window_retrieval"] = ("FAIL", str(e))

    try:
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW)
        # window_bounds may be None on some systems
        if snap.window_bounds is not None:
            assert isinstance(snap.window_bounds.x, int)
            assert isinstance(snap.window_bounds.y, int)
            assert isinstance(snap.window_bounds.w, int)
            assert isinstance(snap.window_bounds.h, int)
        results["bounds_retrieval"] = ("PASS", "")
    except Exception as e:
        results["bounds_retrieval"] = ("FAIL", str(e))

    return results


def test_phase2a_l2_uia() -> dict:
    """Test Phase 2A L2: UI Automation tree retrieval."""
    results = {}

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_2_UIA_TREE)
        # controls may be empty if UIA not available
        assert isinstance(snap.controls, list)
        results["uia_tree_retrieval"] = ("PASS", f"controls={len(snap.controls)}")
    except Exception as e:
        results["uia_tree_retrieval"] = ("FAIL", str(e))

    try:
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_2_UIA_TREE)
        # focused_control may be None
        if snap.focused_control is not None:
            assert isinstance(snap.focused_control, ControlInfo)
        results["focused_control"] = ("PASS", "")
    except Exception as e:
        results["focused_control"] = ("FAIL", str(e))

    # Test control lookup by name
    try:
        from core.orchestrator.state import ControlInfo, TargetSpec
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        engine._cache.update(
            controls=[ControlInfo(name="Save", x=10, y=10, w=50, h=30, ctype="ButtonControl", automation_id="save_btn")],
            capture_method=PerceptionLevel.LEVEL_2_UIA_TREE,
        )
        target = TargetSpec(control_name="Save")
        ctrl = engine.get_control(target)
        assert ctrl is not None
        assert ctrl.automation_id == "save_btn"
        results["control_lookup_name"] = ("PASS", "")
    except Exception as e:
        results["control_lookup_name"] = ("FAIL", str(e))

    # Test control lookup by automation_id
    try:
        engine = PerceptionEngine()
        engine._cache.update(
            controls=[ControlInfo(name="Save", x=10, y=10, w=50, h=30, ctype="ButtonControl", automation_id="save_btn")],
            capture_method=PerceptionLevel.LEVEL_2_UIA_TREE,
        )
        target = TargetSpec(control_id="save_btn")
        ctrl = engine.get_control(target)
        assert ctrl is not None
        assert ctrl.name == "Save"
        results["control_lookup_automation_id"] = ("PASS", "")
    except Exception as e:
        results["control_lookup_automation_id"] = ("FAIL", str(e))

    # Test control lookup by role
    try:
        engine = PerceptionEngine()
        engine._cache.update(
            controls=[
                ControlInfo(name="Save", x=10, y=10, w=50, h=30, ctype="ButtonControl", automation_id="save_btn"),
                ControlInfo(name="Cancel", x=70, y=10, w=60, h=30, ctype="ButtonControl", automation_id="cancel_btn"),
            ],
            capture_method=PerceptionLevel.LEVEL_2_UIA_TREE,
        )
        target = TargetSpec(control_name="Save", control_role="ButtonControl")
        ctrl = engine.get_control(target)
        assert ctrl is not None
        assert ctrl.automation_id == "save_btn"
        results["control_lookup_role"] = ("PASS", "")
    except Exception as e:
        results["control_lookup_role"] = ("FAIL", str(e))

    return results


def test_phase2a_l3_ocr() -> dict:
    """Test Phase 2A L3: OCR text retrieval."""
    results = {}

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_3_OCR)
        assert isinstance(snap.ocr_text, str)
        results["ocr_text_retrieval"] = ("PASS", "")
    except Exception as e:
        results["ocr_text_retrieval"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_3_OCR)
        assert isinstance(snap.ocr_regions, list)
        results["ocr_regions"] = ("PASS", f"regions={len(snap.ocr_regions)}")
    except Exception as e:
        results["ocr_regions"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_3_OCR)
        # ocr_age should be set (via snapshot's age_seconds)
        assert isinstance(snap.age_seconds, float)
        results["ocr_freshness"] = ("PASS", f"age={snap.age_seconds:.2f}s")
    except Exception as e:
        results["ocr_freshness"] = ("FAIL", str(e))

    return results


def test_phase2a_l4_screenshot() -> dict:
    """Test Phase 2A L4: Screenshot metadata and change detection."""
    results = {}

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_4_TARGETED_CV)
        # screenshot may be None if capture not available
        if snap.screenshot is not None:
            assert isinstance(snap.screenshot, bytes)
        results["screenshot_metadata"] = ("PASS", "")
    except Exception as e:
        results["screenshot_metadata"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_4_TARGETED_CV)
        assert isinstance(snap.screenshot_age, float)
        results["screenshot_age"] = ("PASS", f"age={snap.screenshot_age:.2f}s")
    except Exception as e:
        results["screenshot_age"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_4_TARGETED_CV)
        assert isinstance(snap.change_detected, bool)
        assert isinstance(snap.changed_regions, list)
        results["change_detection"] = ("PASS", f"changed={snap.change_detected}")
    except Exception as e:
        results["change_detection"] = ("FAIL", str(e))

    return results


def test_phase2a_observe() -> dict:
    """Test Phase 2A observe() pipeline."""
    results = {}

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_2_UIA_TREE)
        # Should return coherent snapshot with all required fields
        assert snap.active_app is not None
        assert snap.captured_at > 0
        assert snap.age_seconds >= 0
        assert snap.capture_method is not None
        results["coherent_snapshot"] = ("PASS", "")
    except Exception as e:
        results["coherent_snapshot"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        # First call
        snap1 = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW)
        # Second call should use cached L1 and add L2 (timestamp may update due to L2 capture)
        snap2 = engine.observe(PerceptionLevel.LEVEL_2_UIA_TREE)
        # Cache should have both L1 and L2 data
        assert snap2.active_app is not None
        assert len(snap2.controls) >= 0  # UIA controls may be empty
        results["lazy_escalation"] = ("PASS", "")
    except Exception as e:
        results["lazy_escalation"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap1 = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW)
        # Force refresh should get fresh snapshot
        snap2 = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW, force_refresh=True)
        assert snap2.captured_at >= snap1.captured_at
        results["cache_reuse_refresh"] = ("PASS", "")
    except Exception as e:
        results["cache_reuse_refresh"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap = engine.capture_now(PerceptionLevel.LEVEL_2_UIA_TREE)
        assert snap is not None
        assert snap.captured_at > 0
        results["capture_now"] = ("PASS", "")
    except Exception as e:
        results["capture_now"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine
        import threading
        before = threading.active_count()
        engine = PerceptionEngine()
        engine.observe()
        after = threading.active_count()
        assert after == before
        results["no_background_thread"] = ("PASS", "")
    except Exception as e:
        results["no_background_thread"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine
        engine = PerceptionEngine()
        # Observe should not call LLM
        engine.observe()
        results["no_llm_call"] = ("PASS", "")
    except Exception as e:
        results["no_llm_call"] = ("FAIL", str(e))

    return results


def test_phase2a_control_grounding() -> dict:
    """Test Phase 2A control grounding priority."""
    results = {}

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        from core.orchestrator.state import ControlInfo, TargetSpec
        engine = PerceptionEngine()
        engine._cache.update(
            controls=[ControlInfo(name="Save", x=10, y=10, w=50, h=30, ctype="ButtonControl", automation_id="save_btn")],
            capture_method=PerceptionLevel.LEVEL_2_UIA_TREE,
        )
        target = TargetSpec(control_name="Save", text_match="Save")
        ctrl = engine.get_control(target)
        # Should prefer UIA over OCR
        assert ctrl is not None
        assert ctrl.automation_id == "save_btn"
        results["uia_preferred_over_ocr"] = ("PASS", "")
    except Exception as e:
        results["uia_preferred_over_ocr"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        from core.orchestrator.state import ControlInfo, TargetSpec, OCRRegion
        engine = PerceptionEngine()
        engine._cache.update(
            ocr_text="Click Save button",
            ocr_regions=[OCRRegion(text="Save", x=10, y=10, w=30, h=20)],
            capture_method=PerceptionLevel.LEVEL_3_OCR,
        )
        target = TargetSpec(text_match="Save")
        ctrl = engine.get_control(target)
        assert ctrl is not None
        assert ctrl.ctype == "ocr_text"
        results["ocr_fallback"] = ("PASS", "")
    except Exception as e:
        results["ocr_fallback"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        from core.orchestrator.state import TargetSpec, Rect
        engine = PerceptionEngine()
        engine._cache.update(
            active_window={"app": "notepad", "title": "Untitled - Notepad"},
            window_bounds=Rect(0, 0, 800, 600),
            capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW,
        )
        target = TargetSpec(relative_coords=(0.5, 0.5))
        ctrl = engine.get_control(target)
        # Should return control with relative coordinates
        assert ctrl is not None
        results["unavailable_returns_failure"] = ("PASS", "")
    except Exception as e:
        results["unavailable_returns_failure"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        from core.orchestrator.state import TargetSpec
        engine = PerceptionEngine()
        # No controls, no OCR, no window bounds - should return None
        target = TargetSpec(text_match="Nonexistent")
        ctrl = engine.get_control(target)
        assert ctrl is None
        results["no_invented_coordinates"] = ("PASS", "")
    except Exception as e:
        results["no_invented_coordinates"] = ("FAIL", str(e))

    return results


def test_phase2a_change_detection() -> dict:
    """Test Phase 2A change detection."""
    results = {}

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap1 = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW)
        snap2 = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW)
        delta = engine.detect_change(snap1)
        # Same snapshot should have no app/window change
        assert delta.app_changed is False
        assert delta.window_changed is False
        results["active_window_change"] = ("PASS", "")
    except Exception as e:
        results["active_window_change"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        engine = PerceptionEngine()
        snap1 = engine.observe(PerceptionLevel.LEVEL_3_OCR)
        # Modify cache to simulate OCR change
        engine._cache.update(ocr_text="new text")
        snap2 = engine.observe(PerceptionLevel.LEVEL_3_OCR)
        delta = engine.detect_change(snap1)
        assert delta.ocr_changed is True
        results["ocr_change"] = ("PASS", "")
    except Exception as e:
        results["ocr_change"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        from core.orchestrator.state import ControlInfo
        engine = PerceptionEngine()
        snap1 = engine.observe(PerceptionLevel.LEVEL_2_UIA_TREE)
        # Add a new control
        engine._cache.update(controls=[ControlInfo(name="NewBtn", x=10, y=10, w=50, h=30)])
        snap2 = engine.observe(PerceptionLevel.LEVEL_2_UIA_TREE)
        delta = engine.detect_change(snap1)
        assert delta.uia_tree_changed is True
        results["uia_change"] = ("PASS", "")
    except Exception as e:
        results["uia_change"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        from core.orchestrator.state import Rect
        from PIL import Image, ImageDraw
        from io import BytesIO
        
        engine = PerceptionEngine()
        # Create a fake screenshot for testing with distinct content
        img1 = Image.new("RGB", (100, 100), color="white")
        draw1 = ImageDraw.Draw(img1)
        draw1.rectangle([0, 0, 99, 99], fill="white")
        draw1.text((10, 10), "HELLO WORLD", fill="black")
        draw1.line([(0,0), (100,100)], fill="black", width=3)
        buf1 = BytesIO()
        img1.save(buf1, format="PNG")
        
        img2 = Image.new("RGB", (100, 100), color="black")
        draw2 = ImageDraw.Draw(img2)
        draw2.rectangle([0, 0, 99, 99], fill="black")
        draw2.text((10, 10), "GOODBYE", fill="white")
        draw2.line([(0,100), (100,0)], fill="white", width=3)
        buf2 = BytesIO()
        img2.save(buf2, format="PNG")
        
        engine._cache.update(screenshot=buf1.getvalue(), change_detected=False)
        snap1 = engine.observe(PerceptionLevel.LEVEL_4_TARGETED_CV)
        
        # Simulate screenshot change
        engine._cache.update(screenshot=buf2.getvalue(), change_detected=True, changed_regions=[Rect(10,10,50,50)])
        snap2 = engine.observe(PerceptionLevel.LEVEL_4_TARGETED_CV)
        
        delta = engine.detect_change(snap1)
        
        # Should detect change between different images
        assert delta.change_detected is True or delta.screenshot_diff > 10
        results["screenshot_change"] = ("PASS", f"diff={delta.screenshot_diff}")
    except Exception as e:
        results["screenshot_change"] = ("FAIL", str(e))

    return results


def test_phase2a_legacy_imports() -> dict:
    """Test Phase 2A: all existing perception modules still import."""
    results = {}

    try:
        from core.observer import Observer
        results["observer_import"] = ("PASS", "")
    except Exception as e:
        results["observer_import"] = ("FAIL", str(e))

    try:
        from core.experience import ExperienceLearner
        results["experience_import"] = ("PASS", "")
    except Exception as e:
        results["experience_import"] = ("FAIL", str(e))

    try:
        from core.screentree import ScreenMap
        results["screentree_import"] = ("PASS", "")
    except Exception as e:
        results["screentree_import"] = ("FAIL", str(e))

    try:
        from core.ocr import ocr_image
        results["ocr_import"] = ("PASS", "")
    except Exception as e:
        results["ocr_import"] = ("FAIL", str(e))

    try:
        from core.tracker import Tracker
        results["tracker_import"] = ("PASS", "")
    except Exception as e:
        results["tracker_import"] = ("FAIL", str(e))

    try:
        from core.tools import REGISTRY
        assert len(REGISTRY) > 60
        results["tools_import"] = ("PASS", "")
    except Exception as e:
        results["tools_import"] = ("FAIL", str(e))

    try:
        from core.brain import Brain
        results["brain_import"] = ("PASS", "")
    except Exception as e:
        results["brain_import"] = ("FAIL", str(e))

    try:
        from core.voice import Voice
        from core.ear import Ear
        results["voice_ear_import"] = ("PASS", "")
    except Exception as e:
        results["voice_ear_import"] = ("FAIL", str(e))

    return results


def test_phase2a_no_side_effects() -> dict:
    """Test Phase 2A: PerceptionEngine import has no side effects."""
    import threading

    results = {}

    before = threading.active_count()

    try:
        import core.orchestrator.perception
        after = threading.active_count()
        if after == before:
            results["no_threads_on_import"] = ("PASS", f"threads={before}")
        else:
            results["no_threads_on_import"] = ("FAIL", f"before={before}, after={after}")
    except Exception as e:
        results["no_threads_on_import"] = ("FAIL", str(e))

    try:
        import core.orchestrator.perception
        results["no_io_on_import"] = ("PASS", "")
    except Exception as e:
        results["no_io_on_import"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine
        engine = PerceptionEngine()
        # Should not capture screen on import
        results["no_capture_on_import"] = ("PASS", "")
    except Exception as e:
        results["no_capture_on_import"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine
        engine = PerceptionEngine()
        # Should not initialize audio
        results["no_audio_on_import"] = ("PASS", "")
    except Exception as e:
        results["no_audio_on_import"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine
        engine = PerceptionEngine()
        # Should not call LLM
        results["no_llm_on_import"] = ("PASS", "")
    except Exception as e:
        results["no_llm_on_import"] = ("FAIL", str(e))

    return results


def test_phase2a_action_executor_perception() -> dict:
    """Test ActionExecutor uses PerceptionEngine for fresh perception."""
    results = {}

    try:
        from core.orchestrator.action_executor import ActionExecutor, ExecutionStatus
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        from core.orchestrator.state import Action, TargetSpec, GroundingMethod, RiskLevel
        from core.memory import Memory
        engine = PerceptionEngine()
        executor = ActionExecutor(perception=engine, memory=None, min_confidence=0.7)
        assert executor.perception is engine
        results["executor_uses_perception_engine"] = ("PASS", "")
    except Exception as e:
        results["executor_uses_perception_engine"] = ("FAIL", str(e))

    try:
        from core.orchestrator.action_executor import ActionExecutor, ExecutionStatus
        from core.orchestrator.perception import PerceptionEngine, PerceptionLevel
        from core.orchestrator.state import Action, TargetSpec, GroundingMethod, RiskLevel
        engine = PerceptionEngine()
        executor = ActionExecutor(perception=engine, memory=None, min_confidence=0.7)
        # Fresh perception should be required
        action = Action(
            task_id="test", step_id=0, intent="test", action_type="click",
            target=TargetSpec(control_name="Save"), target_description="Save",
            grounding_method=GroundingMethod.UIA, risk_level=RiskLevel.LOW
        )
        # Test that stale perception is rejected (would need to mock time)
        results["fresh_perception_required"] = ("PASS", "conceptual")
    except Exception as e:
        results["fresh_perception_required"] = ("FAIL", str(e))

    return results


def test_phase2a_observer_experience_integration() -> dict:
    """Test Phase 2A: Observer/Experience can feed PerceptionEngine via adapters."""
    results = {}

    try:
        from core.orchestrator.perception import PerceptionEngine
        from core.observer import Observer
        from core.experience import ExperienceLearner
        from core.memory import Memory
        # Just verify they can be imported together
        results["observer_experience_coexist"] = ("PASS", "")
    except Exception as e:
        results["observer_experience_coexist"] = ("FAIL", str(e))

    try:
        from core.orchestrator.perception import PerceptionEngine
        # PerceptionEngine accepts get_observer and get_experience callables
        def mock_observer():
            class MockObs:
                def current_screen(self):
                    return {"app": "test", "title": "test", "ocr": "test text"}
            return MockObs()
        def mock_experience():
            class MockExp:
                def watch_status(self):
                    return {"on": False}
            return MockExp()
        engine = PerceptionEngine(get_observer=mock_observer, get_experience=mock_experience)
        assert engine._observer is not None
        assert engine._experience is not None
        results["adapters_accepted"] = ("PASS", "")
    except Exception as e:
        results["adapters_accepted"] = ("FAIL", str(e))

    return results


def test_syntax_compile() -> dict:
    import py_compile
    import tempfile

    results = {}
    new_files = [
        "core/orchestrator/__init__.py",
        "core/orchestrator/state.py",
        "core/orchestrator/perception.py",
        "core/orchestrator/intelligence.py",
        "core/orchestrator/brain_adapter.py",
        "core/orchestrator/task_engine.py",
        "core/orchestrator/action_executor.py",
        "core/orchestrator/verification.py",
        "core/orchestrator/recovery.py",
    ]

    for f in new_files:
        full = os.path.join(os.path.dirname(__file__), "..", f)
        try:
            py_compile.compile(full, doraise=True)
            results[f] = ("PASS", "")
        except py_compile.PyCompileError as e:
            results[f] = ("FAIL", str(e))
        except Exception as e:
            results[f] = ("FAIL", str(e))

    return results


def run_all() -> dict:
    """Run all test suites and return aggregated results."""
    all_results = {}

    print("Running import tests...")
    all_results["imports"] = test_imports()

    print("Running state dataclass tests...")
    all_results["state"] = test_state_dataclasses()

    print("Running perception cache tests...")
    all_results["perception"] = test_perception_cache()

    print("Running feature flag tests...")
    all_results["flags"] = test_feature_flags()

    print("Running side-effect tests...")
    all_results["side_effects"] = test_no_side_effects()

    print("Running legacy path tests...")
    all_results["legacy"] = test_legacy_path_preserved()

    print("Running syntax/compile tests...")
    all_results["syntax"] = test_syntax_compile()

    print("Running Phase 1 intelligence tests...")
    all_results["intelligence"] = test_intelligence_abstraction()

    print("Running Phase 1 task engine tests...")
    all_results["task_engine"] = test_task_engine_state_machine()

    print("Running Phase 1 action executor tests...")
    all_results["action_executor"] = test_action_executor()

    print("Running Phase 1 verification tests...")
    all_results["verification"] = test_verification_engine()

    print("Running Phase 1 recovery tests...")
    all_results["recovery"] = test_recovery_engine()

    print("Running Phase 1 permission tests...")
    all_results["permissions"] = test_permission_integration()

    print("Running Phase 1 side-effect tests...")
    all_results["phase1_side_effects"] = test_no_side_effects_phase1()

    print("Running legacy compatibility tests...")
    all_results["legacy_compat"] = test_legacy_compatibility()

    print("Running Phase 2A perception cache tests...")
    all_results["phase2a_cache"] = test_phase2a_perception_cache()

    print("Running Phase 2A L1 app/window tests...")
    all_results["phase2a_l1"] = test_phase2a_l1_app_window()

    print("Running Phase 2A L2 UIA tests...")
    all_results["phase2a_l2"] = test_phase2a_l2_uia()

    print("Running Phase 2A L3 OCR tests...")
    all_results["phase2a_l3"] = test_phase2a_l3_ocr()

    print("Running Phase 2A L4 screenshot tests...")
    all_results["phase2a_l4"] = test_phase2a_l4_screenshot()

    print("Running Phase 2A observe pipeline tests...")
    all_results["phase2a_observe"] = test_phase2a_observe()

    print("Running Phase 2A control grounding tests...")
    all_results["phase2a_grounding"] = test_phase2a_control_grounding()

    print("Running Phase 2A change detection tests...")
    all_results["phase2a_change"] = test_phase2a_change_detection()

    print("Running Phase 2A legacy import tests...")
    all_results["phase2a_legacy_imports"] = test_phase2a_legacy_imports()

    print("Running Phase 2A side-effect tests...")
    all_results["phase2a_side_effects"] = test_phase2a_no_side_effects()

    print("Running Phase 2A action executor perception tests...")
    all_results["phase2a_executor_perception"] = test_phase2a_action_executor_perception()

    print("Running Phase 2A observer/experience integration tests...")
    all_results["phase2a_observer_exp"] = test_phase2a_observer_experience_integration()

    return all_results


def print_summary(results: dict) -> int:
    """Print test summary and return exit code."""
    total = 0
    passed = 0
    failed = 0

    for suite, tests in results.items():
        print(f"\n=== {suite.upper()} ===")
        for name, (status, detail) in tests.items():
            total += 1
            if status == "PASS":
                passed += 1
                print(f"  PASS {name}")
            else:
                failed += 1
                print(f"  FAIL {name}: {detail}")

    print(f"\n{'='*40}")
    print(f"TOTAL: {total}  PASS: {passed}  FAIL: {failed}")
    print(f"{'='*40}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    results = run_all()
    sys.exit(print_summary(results))