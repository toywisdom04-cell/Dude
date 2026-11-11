"""TaskEngine - Authoritative task runtime state machine.

Orchestrates the complete task lifecycle using structured state transitions.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

from .state import (
    TaskState,
    OrchestratorState,
    VoiceState,
    SubGoal,
    Action,
    TargetSpec,
    ExpectedResult,
    VerificationMethod,
    GroundingMethod,
    RiskLevel,
    TaskType,
    InterruptRelation,
    UnexpectedState,
    PermissionState,
    PerceptionLevel,
    StepResult,
)
from .perception import PerceptionEngine
from .action_executor import ActionExecutor, ActionExecutionResult
from .verification import VerificationEngine, VerificationResult
from .recovery import RecoveryEngine, RecoveryDecision
from .recovery_action_executor import RecoveryActionExecutor
from .dialogs import classify_dialog, DialogKind
from .naming import validate_filename
from .intelligence import IntelligenceBackend, GenerateRequest
from .intelligence_router import IntelligenceRouter, RouteDecision, RoutingResult
from .state import Plan
from .skills import get_skill_registry
from .procedure_store import get_procedure_store
from .procedure_learner import get_procedure_learner
from .procedure_adaptation import get_adaptation_manager
from core.runtime_repair import propose_repair
from core.config import get_config


log = logging.getLogger(__name__)


class TransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


ACTION_FENCE_OPEN = "OPEN"
ACTION_FENCE_BLOCKED_RUNTIME = "BLOCKED_RUNTIME"
ACTION_FENCE_BLOCKED_RECOVERY = "BLOCKED_RECOVERY"
ACTION_FENCE_BLOCKED_VERIFICATION = "BLOCKED_VERIFICATION"
ACTION_FENCE_RELEASED = "RELEASED"

ACTION_ALLOW = "ALLOW"
ACTION_BLOCK_RUNTIME = "BLOCK_RUNTIME"
ACTION_BLOCK_RECOVERY = "BLOCK_RECOVERY"
ACTION_BLOCK_VERIFICATION = "BLOCK_VERIFICATION"
ACTION_HANDOFF_REQUIRED = "HANDOFF_REQUIRED"
ACTION_ESCALATE = "ESCALATE"

SIDE_EFFECT_READ_ONLY = "READ_ONLY"
SIDE_EFFECT_IDEMPOTENT = "IDEMPOTENT"
SIDE_EFFECT_NON_IDEMPOTENT = "NON_IDEMPOTENT"
SIDE_EFFECT_UNKNOWN = "UNKNOWN"
REPLAY_VERIFY_FIRST = "VERIFY_FIRST"
REPLAY_SAFE_RETRY = "SAFE_RETRY"
REPLAY_DO_NOT_RETRY = "DO_NOT_RETRY"


@dataclass(frozen=True)
class ActionFenceDecision:
    """One current deterministic decision for a specific planned action."""

    state: str
    decision: str
    allowed: bool
    reason: str
    required_capabilities: tuple[str, ...]
    blocking_capability: str
    task_step: str
    verification_basis: str
    provenance: str
    checked_at: float

    @classmethod
    def initial(cls) -> "ActionFenceDecision":
        return cls(
            state=ACTION_FENCE_OPEN,
            decision=ACTION_ALLOW,
            allowed=True,
            reason="no action has been evaluated",
            required_capabilities=(),
            blocking_capability="",
            task_step="",
            verification_basis="none",
            provenance="TaskEngine+ActionFence",
            checked_at=0.0,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "decision": self.decision,
            "allowed": bool(self.allowed),
            "reason": self.reason,
            "required_capabilities": list(self.required_capabilities),
            "blocking_capability": self.blocking_capability,
            "task_step": self.task_step,
            "verification_basis": self.verification_basis,
            "provenance": self.provenance,
            "checked_at": float(self.checked_at),
        }


# Valid state transitions. Every active state can also return to IDLE:
# that edge is user cancellation (stop), which preserves task state
# instead of failing the task.
VALID_TRANSITIONS = {
    OrchestratorState.IDLE: {OrchestratorState.LISTENING},
    OrchestratorState.LISTENING: {OrchestratorState.UNDERSTANDING, OrchestratorState.IDLE},
    OrchestratorState.UNDERSTANDING: {OrchestratorState.OBSERVING, OrchestratorState.FAILED, OrchestratorState.IDLE},
    OrchestratorState.OBSERVING: {OrchestratorState.PLANNING, OrchestratorState.FAILED, OrchestratorState.IDLE},
    OrchestratorState.PLANNING: {OrchestratorState.ACTING, OrchestratorState.FAILED, OrchestratorState.IDLE},
    OrchestratorState.ACTING: {OrchestratorState.VERIFYING, OrchestratorState.FAILED, OrchestratorState.IDLE},
    OrchestratorState.VERIFYING: {
        OrchestratorState.OBSERVING,  # More steps
        OrchestratorState.DONE,       # Complete
        OrchestratorState.RECOVERING, # Failed
        OrchestratorState.IDLE,       # Cancelled
    },
    OrchestratorState.RECOVERING: {
        OrchestratorState.OBSERVING,  # Retry
        OrchestratorState.FAILED,     # Max retries exceeded
        OrchestratorState.IDLE,       # Cancelled
    },
    OrchestratorState.SPEAKING: {OrchestratorState.INTERRUPTED, OrchestratorState.IDLE},
    OrchestratorState.INTERRUPTED: {OrchestratorState.UNDERSTANDING, OrchestratorState.IDLE},
    OrchestratorState.DONE: {OrchestratorState.IDLE},
    OrchestratorState.FAILED: {OrchestratorState.IDLE, OrchestratorState.UNDERSTANDING},
}


class TaskEngine:
    """Authoritative task runtime - orchestrates the complete task lifecycle."""

    # These are deliberately conservative guardrails for one task run. They
    # bound the observe/decide/act/verify loop without changing the existing
    # action fence, permission gate, or recovery policy.
    DEFAULT_MAX_ACTIONS = 32
    DEFAULT_MAX_ITERATIONS = 128
    DEFAULT_NO_PROGRESS_LIMIT = 8
    DEFAULT_REPEATED_PROBE_LIMIT = 3
    
    def __init__(
        self,
        intelligence: IntelligenceBackend,
        perception: PerceptionEngine,
        action_executor: "ActionExecutor",
        verification: "VerificationEngine",
        recovery: "RecoveryEngine",
        permission_gate: Optional[Any] = None,
        voice: Optional[Any] = None,
        memory: Optional[Any] = None,
        max_retries: int = 3,
        perception_freshness_seconds: float = 5.0,
        retry_delays: tuple = (1.0, 3.0, 10.0),
        intelligence_router: Optional["IntelligenceRouter"] = None,
        procedure_learner: Optional["ProcedureLearner"] = None,
        adaptation_manager: Optional["ProcedureAdaptationManager"] = None,
        recovery_action_executor: Optional["RecoveryActionExecutor"] = None,
        use_real_execution: bool = False,
        visual_state_bus: Optional[Any] = None,
        agent_state: Optional[Any] = None,
        max_actions: int = DEFAULT_MAX_ACTIONS,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        no_progress_limit: int = DEFAULT_NO_PROGRESS_LIMIT,
        repeated_probe_limit: int = DEFAULT_REPEATED_PROBE_LIMIT,
    ):
        self.intelligence = intelligence
        self.perception = perception
        self.action_executor = action_executor
        self.verification = verification
        self.recovery = recovery
        self.cap_bus = None
        self._action_fence_enabled = False
        self._action_fence = ActionFenceDecision.initial()
        self.handoff_notifier: Optional[Callable[[Dict[str, Any]], None]] = None
        self.permission_gate = permission_gate
        self.voice = voice
        self.memory = memory
        
        self.max_retries = max_retries
        self.perception_freshness_seconds = perception_freshness_seconds
        self.retry_delays = retry_delays
        
        # Phase 2B: Intelligence Router (optional, feature-gated)
        self.intelligence_router = intelligence_router
        
        # Phase 2E: Procedure Learner (optional, feature-gated)
        self.procedure_learner = procedure_learner
        
        # Phase 2F: Procedure Adaptation Manager (optional, feature-gated)
        self.adaptation_manager = adaptation_manager
        
        # Phase 3: Real execution mode (feature-gated)
        self.use_real_execution = use_real_execution
        
        # Phase 3: Recovery action executor
        self.recovery_action_executor = recovery_action_executor

        # Phase 14: Visual State Bus (optional, additive). Every
        # observation taken for a task also replaces the bus's current
        # state so reasoning, answers, and UI read one live state.
        self.visual_state_bus = visual_state_bus
        self.agent_state = agent_state

        # Per-task limits are copied into TaskState.metrics when a task is
        # created. Keeping the values on the engine also preserves the public
        # constructor/API shape for existing callers while allowing focused
        # harnesses to choose smaller budgets.
        self.max_actions = max(0, int(max_actions))
        self.max_iterations = max(1, int(max_iterations))
        self.no_progress_limit = max(1, int(no_progress_limit))
        self.repeated_probe_limit = max(1, int(repeated_probe_limit))
        self._bounded_task_id: Optional[str] = None
        self._last_progress_marker = None
        self._last_probe_signature = None
        
        self._state = OrchestratorState.IDLE
        self._task_state: Optional[TaskState] = None
        self._pending_hand_action: Optional[Action] = None
        self._last_hand_action_feedback: Optional[dict] = None
        self._hand_dispatch_thread = None
        self._running = False
        self._paused = False
        self._runtime_handoff_event: Optional[asyncio.Event] = None
        self._runtime_handoff_loop = None
        self._runtime_handoff_capability = ""
        _interrupt_received = False
    
    @property
    def state(self) -> OrchestratorState:
        return self._state
    
    @property
    def task_state(self) -> Optional[TaskState]:
        return self._task_state
    
    def _transition(self, new_state: OrchestratorState) -> None:
        """Perform a validated state transition."""
        if new_state not in VALID_TRANSITIONS.get(self._state, set()):
            raise TransitionError(
                f"Invalid transition: {self._state.value} -> {new_state.value}. "
                f"Valid: {[s.value for s in VALID_TRANSITIONS.get(self._state, set())]}"
            )
        log.debug(f"State transition: {self._state.value} -> {new_state.value}")
        self._state = new_state
        if self._task_state:
            self._task_state.updated_at = time.time()
    
    def start_task(self, goal: str, goal_type: TaskType = TaskType.UNKNOWN,
                   prefer_fresh_windows: bool = False,
                   execution_mode: str = "foreground") -> TaskState:
        """Create and start a new task."""
        if self._state != OrchestratorState.IDLE:
            raise TransitionError(f"Cannot start task from {self._state.value}")

        clear_continuity = getattr(self.agent_state, "clear_task_continuity", None)
        if callable(clear_continuity):
            clear_continuity()
        self._task_state = TaskState(goal=goal, goal_type=goal_type)
        self._task_state.voice_state = VoiceState.LISTENING
        self._task_state.prefer_fresh_windows = prefer_fresh_windows
        self._task_state.execution_mode = execution_mode
        self._initialize_task_bounds(self._task_state, reset=True)
        self._running = True
        self._paused = False
        self._transition(OrchestratorState.LISTENING)
        return self._task_state

    def _initialize_task_bounds(self, task: Optional[TaskState] = None,
                                *, reset: bool = False) -> None:
        """Initialize the bounded-loop bookkeeping for one TaskState.

        TaskState is the authoritative per-task object, so counters live in
        its existing ``metrics`` dictionary rather than in a second state
        store. ``reset=False`` is used by resume() for tests and integrations
        that attach a TaskState directly.
        """
        task = task or self._task_state
        if task is None:
            return
        metrics = getattr(task, "metrics", None)
        if not isinstance(metrics, dict):
            task.metrics = {}
            metrics = task.metrics
        if reset or self._bounded_task_id != str(getattr(task, "task_id", "")):
            metrics.setdefault("actions", 0)
            metrics.setdefault("recovery_actions", 0)
            metrics["iterations"] = 0
            metrics["no_progress_streak"] = 0
            metrics["repeated_probe_streak"] = 0
            if reset:
                metrics["max_actions"] = self.max_actions
                metrics["max_iterations"] = self.max_iterations
                metrics["no_progress_limit"] = self.no_progress_limit
                metrics["repeated_probe_limit"] = self.repeated_probe_limit
            else:
                metrics.setdefault("max_actions", self.max_actions)
                metrics.setdefault("max_iterations", self.max_iterations)
                metrics.setdefault("no_progress_limit", self.no_progress_limit)
                metrics.setdefault("repeated_probe_limit", self.repeated_probe_limit)
            self._bounded_task_id = str(getattr(task, "task_id", ""))
            self._last_progress_marker = self._progress_marker(task)
            self._last_probe_signature = None
        else:
            # Do not overwrite an explicit per-task override supplied by a
            # caller through the existing metrics bag.
            metrics.setdefault("max_actions", self.max_actions)
            metrics.setdefault("max_iterations", self.max_iterations)
            metrics.setdefault("no_progress_limit", self.no_progress_limit)
            metrics.setdefault("repeated_probe_limit", self.repeated_probe_limit)
            metrics.setdefault("iterations", 0)
            metrics.setdefault("no_progress_streak", 0)
            metrics.setdefault("repeated_probe_streak", 0)
            metrics.setdefault("recovery_actions", 0)

    @staticmethod
    def _progress_marker(task: Optional[TaskState]):
        """Return compact observable task progress, safe for arbitrary mocks."""
        if task is None:
            return None
        action = getattr(task, "last_action", None)
        result = getattr(task, "last_action_result", None)
        verification = getattr(task, "verification_result", None)
        target = getattr(action, "target_description", "") if action else ""
        return (
            int(getattr(task, "current_step", 0) or 0),
            len(getattr(task, "completed_steps", None) or ()),
            int(getattr(task, "recovery_attempts", 0) or 0),
            int((getattr(task, "metrics", None) or {}).get("actions", 0) or 0),
            int((getattr(task, "metrics", None) or {}).get(
                "recovery_actions", 0) or 0),
            (getattr(action, "step_id", None),
             str(getattr(action, "action_type", "") or ""),
             str(target or "")),
            (getattr(result, "success", None), str(getattr(result, "error", "") or "")),
            (getattr(verification, "success", None),
             str(getattr(verification, "method", "") or ""),
             str(getattr(verification, "evidence", "") or "")),
        )

    @staticmethod
    def _probe_signature(snapshot, task: Optional[TaskState]):
        """Fingerprint the read-only observation used for the next decision."""
        if snapshot is None or task is None:
            return None
        window = getattr(snapshot, "active_window", None) or {}
        if not isinstance(window, dict):
            window = {}
        controls = []
        for control in list(getattr(snapshot, "controls", None) or [])[:32]:
            controls.append((
                str(getattr(control, "name", "") or ""),
                str(getattr(control, "ctype", "") or ""),
                int(getattr(control, "x", 0) or 0),
                int(getattr(control, "y", 0) or 0),
                int(getattr(control, "w", 0) or 0),
                int(getattr(control, "h", 0) or 0),
                bool(getattr(control, "enabled", True)),
                bool(getattr(control, "visible", True)),
            ))
        return (
            int(getattr(task, "current_step", 0) or 0),
            str(getattr(snapshot, "active_app", "") or ""),
            str(window.get("title", "") or ""),
            str(window.get("app", "") or ""),
            window.get("hwnd"),
            tuple(controls),
            str(getattr(snapshot, "ocr_text", "") or "")[:400],
        )

    def _fail_bounded(self, reason: str) -> None:
        """Stop an active task with an explicit bounded-runtime reason."""
        task = self._task_state
        if task is not None:
            task.failure_reason = self._continuity_text(reason, 280)
            try:
                task.metrics["terminal_reason"] = task.failure_reason
                task.metrics["terminal_state"] = "FAILED"
                task.recovery_history.append({
                    "phase": "bounded_stop",
                    "reason": task.failure_reason,
                    "timestamp": time.time(),
                })
            except Exception:
                pass
        if self._state not in (OrchestratorState.FAILED,
                               OrchestratorState.DONE,
                               OrchestratorState.IDLE):
            self._transition(OrchestratorState.FAILED)

    def _action_budget_available(self, context: str = "action") -> bool:
        """Check the task action budget before crossing an executor boundary."""
        task = self._task_state
        if task is None:
            return False
        self._initialize_task_bounds(task)
        metrics = task.metrics
        used = int(metrics.get("actions", 0) or 0) + int(
            metrics.get("recovery_actions", 0) or 0)
        limit = max(0, int(metrics.get("max_actions", self.max_actions) or 0))
        if used >= limit:
            self._fail_bounded(
                f"maximum action limit reached ({limit}) before {context}")
            return False
        return True

    def _record_recovery_action(self) -> None:
        if self._task_state is not None:
            metrics = self._task_state.metrics
            metrics["recovery_actions"] = metrics.get("recovery_actions", 0) + 1

    def _record_observation_probe(self, snapshot) -> bool:
        """Track repeated identical observations and fail closed when stuck."""
        task = self._task_state
        if task is None:
            return False
        self._initialize_task_bounds(task)
        signature = self._probe_signature(snapshot, task)
        if signature is None:
            return False
        metrics = task.metrics
        if signature == self._last_probe_signature:
            streak = int(metrics.get("repeated_probe_streak", 0) or 0) + 1
        else:
            streak = 0
        self._last_probe_signature = signature
        metrics["repeated_probe_streak"] = streak
        if streak >= max(1, int(metrics.get(
                "repeated_probe_limit", self.repeated_probe_limit) or 1)):
            self._fail_bounded(
                "no progress: repeated identical observation/probe for "
                f"task step {getattr(task, 'current_step', 0)}")
            return True
        return False
    
    def handle_user_input(self, text: str) -> None:
        """Process user utterance - entry point from voice/text input."""
        if not self._task_state:
            self.start_task(text)
        
        self._task_state.user_interrupt = text
        self._transition(OrchestratorState.UNDERSTANDING)
    
    def handle_interrupt(self, utterance: str) -> None:
        """Handle barge-in interruption during SPEAKING/ACTING."""
        if self._state in (OrchestratorState.SPEAKING, OrchestratorState.ACTING, OrchestratorState.VERIFYING):
            self._task_state.interrupted_speech = utterance
            self._transition(OrchestratorState.INTERRUPTED)
    
    async def run(self, goal: str, goal_type: TaskType = TaskType.UNKNOWN,
                  prefer_fresh_windows: bool = False,
                  execution_mode: str = "foreground",
                  target_hwnd: int = None) -> TaskState:
        """Run a complete task from start to finish."""
        self.start_task(goal, goal_type,
                        prefer_fresh_windows=prefer_fresh_windows,
                        execution_mode=execution_mode)
        if target_hwnd:
            self._task_state.target_hwnd = target_hwnd
        # Provide the initial user input to transition from LISTENING to UNDERSTANDING
        self.handle_user_input(goal)

        await self.resume()
        return self._task_state

    def set_capability_bus(self, capability_bus: Any) -> None:
        """Connect the existing CapabilityBus; no new truth source is created."""
        self.cap_bus = capability_bus
        # Allow disabling action fence via config for testing
        self._action_fence_enabled = (capability_bus is not None) and not get_config().get("orchestrator", "disable_action_fence", default=False)

    def action_fence_state(self) -> Dict[str, Any]:
        return self._action_fence.to_dict()

    def propose_hand_action(self, action: Action) -> bool:
        """Queue one semantic hand proposal for the current task step.

        This is an adapter boundary only: permission checks, the action
        fence, ActionExecutor, verification, and recovery still run in the
        normal planning/acting path below.
        """
        try:
            task = self._task_state
            if task is None or action is None:
                return False
            if str(getattr(action, "task_id", "")) != str(task.task_id):
                return False
            if int(getattr(action, "step_id", -1)) != int(task.current_step):
                return False
            subgoal = task.current_subgoal()
            if subgoal is None or bool(getattr(subgoal, "completed", False)):
                return False
            if str(getattr(action, "action_type", "")).lower() not in {
                    "click_at", "drag", "scroll", "resize_window",
                    "window_operation", "ui_click", "click",
            }:
                return False
            if (self._pending_hand_action is not None and
                    self._pending_hand_action is action):
                return True
            self._pending_hand_action = action
            try:
                # Stamp dispatch time. A hand click already carries an exact
                # screen coordinate and a resolved target, so it never needs a
                # re-plan; the stamp lets the execution boundary honour it even
                # if the task step advanced while the loop was starting.
                if isinstance(getattr(action, "parameters", None), dict):
                    action.parameters["hand_dispatched_at"] = time.time()
            except Exception:
                pass
            try:
                task.metrics["hand_action_proposals"] = task.metrics.get(
                    "hand_action_proposals", 0) + 1
            except Exception:
                pass
            return True
        except Exception as exc:
            log.warning("hand action proposal rejected: %s", exc)
            return False

    def dispatch_hand_action(self, action: Action) -> bool:
        """Queue a hand action and, when idle, run the existing task loop.

        Hand input is a first-class TaskEngine input.  If there is no voice
        task yet, create one bounded task step here rather than dropping the
        semantic proposal on the floor or creating a second executor.
        Existing active tasks keep their current runner; only the idle-start
        path launches the normal ``resume`` coroutine.
        """
        if action is None:
            return False
        created = False
        try:
            task = self._task_state
            if task is not None and self._task_is_finished(task):
                # A completed task was left in place by the terminal
                # transition, so its subgoal is already flagged completed and
                # propose_hand_action() rejected every subsequent pinch - which
                # is why the first click worked and every later one was
                # silently dropped. Retire the finished task so the next
                # gesture starts clean.
                log.info("retiring finished hand task %s for a new gesture",
                         getattr(task, "task_id", "?"))
                self._task_state = None
                task = None
            if task is None:
                if self._state != OrchestratorState.IDLE:
                    return False
                task = self.start_task("hand desktop interaction")
                task.subgoals = [SubGoal(
                    description=str(getattr(
                        action, "target_description", "hand interaction"))[:240],
                    action_type=str(getattr(action, "action_type", "click")),
                    target_description=str(getattr(
                        action, "target_description", "hand interaction"))[:240],
                )]
                action.task_id = str(task.task_id)
                action.step_id = int(task.current_step)
                self._transition(OrchestratorState.UNDERSTANDING)
                self._transition(OrchestratorState.OBSERVING)
                # Perception is captured by _do_acting on the normal task
                # thread; the camera/sensor thread must not call UI-backed
                # observers directly.
                self._transition(OrchestratorState.PLANNING)
                created = True
            if not self.propose_hand_action(action):
                return False
            if not created:
                return True
            existing = getattr(self, "_hand_dispatch_thread", None)
            if existing is not None and existing.is_alive():
                return True

            def _run_hand_task():
                try:
                    asyncio.run(self.resume())
                except Exception:
                    log.exception("hand action task loop failed")
                finally:
                    self._hand_dispatch_thread = None

            self._hand_dispatch_thread = threading.Thread(
                target=_run_hand_task, daemon=True, name="du-hand-task")
            self._hand_dispatch_thread.start()
            return True
        except Exception as exc:
            log.warning("hand action dispatch rejected: %s", exc)
            return False

    #: A hand action stays executable for this long after it was dispatched,
    #: even if the task step moved on. Long enough to cover the planning and
    #: perception work that happens between the pinch and the acting
    #: boundary, short enough that a stale pinch can never fire much later.
    HAND_ACTION_FRESH_SECONDS = 6.0

    @staticmethod
    def _task_is_finished(task) -> bool:
        """True when a task can accept no further hand action."""
        try:
            if task is None:
                return True
            if bool(getattr(task, "cancelled", False)):
                return True
            subgoals = list(getattr(task, "subgoals", ()) or ())
            if subgoals and all(
                    bool(getattr(s, "completed", False)) for s in subgoals):
                return True
            current = None
            try:
                current = task.current_subgoal()
            except Exception:
                current = None
            return current is None or bool(
                getattr(current, "completed", False))
        except Exception:
            return False

    def _hand_action_is_fresh(self, action: Optional[Action]) -> bool:
        """True when a pending hand action is recent enough to still execute.

        Guards against replaying an old gesture: without the timestamp a
        click from a finished task could fire against whatever the user is
        looking at now.
        """
        if action is None:
            return False
        params = getattr(action, "parameters", {}) or {}
        if not bool(params.get("hand_semantic", False)):
            return False
        if str(getattr(action, "action_type", "")).lower() not in {
                "click_at", "drag", "scroll", "resize_window",
                "window_operation", "ui_click", "click"}:
            return False
        stamp = params.get("hand_dispatched_at")
        if stamp is None:
            return False
        try:
            age = time.time() - float(stamp)
        except Exception:
            return False
        return 0.0 <= age <= self.HAND_ACTION_FRESH_SECONDS

    def _publish_hand_action_feedback(self, action: Action, phase: str,
                                      success: bool, verified: bool = False
                                      ) -> None:
        if not bool((getattr(action, "parameters", {}) or {}).get(
                "hand_semantic", False)):
            return
        target = getattr(action, "target", None)
        self._last_hand_action_feedback = {
            "phase": str(phase or ""),
            "success": bool(success),
            "verified": bool(verified),
            "screen_xy": tuple(getattr(target, "coordinates", ()) or ()),
            "action_type": str(getattr(action, "action_type", "") or ""),
            "timestamp": time.time(),
        }

    def hand_action_feedback(self) -> Optional[dict]:
        feedback = getattr(self, "_last_hand_action_feedback", None)
        return dict(feedback) if isinstance(feedback, dict) else None

    @staticmethod
    def _action_capabilities(action: Action) -> tuple[str, ...]:
        """Derive only capabilities used by the existing action metadata."""
        action_type = str(getattr(action, "action_type", "") or "").lower()
        method_value = getattr(action, "verification_method", "")
        method = str(getattr(method_value, "value", method_value) or "").upper()
        grounding_value = getattr(action, "grounding_method", "")
        grounding = str(getattr(grounding_value, "value", grounding_value) or "").upper()
        required = {"perception", "computer_tools"}
        if action_type not in ActionExecutor.NO_GROUND_ACTIONS:
            required.add("uia")
        if method in {
                "OCR_TEXT_APPEARED", "OCR_TEXT_DISAPPEARED",
        } or "OCR" in grounding:
            required.add("ocr")
        if method in {"SCREEN_DELTA"} or "RELATIVE_COORDS" in grounding:
            required.add("vision")
        if method not in {"", "CUSTOM"}:
            required.add("verification")
        if method in {
                "UIA_STATE_CHANGE", "WINDOW_APPEARED", "WINDOW_DISAPPEARED",
                "WINDOW_GEOMETRY_CHANGED", "FOCUSED_CONTROL",
        }:
            required.add("uia")
        return tuple(sorted(required))

    @staticmethod
    def side_effect_contract(action: Optional[Action]) -> Dict[str, str]:
        """Resolve a bounded replay policy from existing action metadata."""
        if action is None:
            return {
                "side_effect_class": SIDE_EFFECT_UNKNOWN,
                "replay_policy": REPLAY_VERIFY_FIRST,
            }
        action_type = str(getattr(action, "action_type", "") or "").lower()
        explicit_class = str(
            getattr(action, "side_effect_class", "") or "").upper()
        explicit_replay = str(
            getattr(action, "replay_policy", "") or "").upper()
        read_only = {
            "screenshot", "get_datetime", "list_running_apps",
            "list_directory", "search_files", "read_file", "clipboard_read",
            "verify_file", "text_read",
        }
        idempotent = {
            "open_app", "open_application", "create_folder",
            "create_directory", "make_folder", "refocus",
        }
        non_idempotent = {
            "click", "ui_click", "click_at", "drag", "scroll",
            "resize_window", "type_text", "write_file", "delete_path",
            "move_path", "copy_path", "submit", "send", "press_hotkey",
            "hotkey", "run_powershell",
        }
        if explicit_class in {
                SIDE_EFFECT_READ_ONLY, SIDE_EFFECT_IDEMPOTENT,
                SIDE_EFFECT_NON_IDEMPOTENT, SIDE_EFFECT_UNKNOWN,
        } and explicit_class != SIDE_EFFECT_UNKNOWN:
            effect = explicit_class
        elif action_type in read_only:
            effect = SIDE_EFFECT_READ_ONLY
        elif action_type in idempotent:
            effect = SIDE_EFFECT_IDEMPOTENT
        elif action_type in non_idempotent:
            effect = SIDE_EFFECT_NON_IDEMPOTENT
        else:
            effect = SIDE_EFFECT_UNKNOWN
        default_replay = (
            REPLAY_SAFE_RETRY if effect in {
                SIDE_EFFECT_READ_ONLY, SIDE_EFFECT_IDEMPOTENT}
            else REPLAY_DO_NOT_RETRY
            if effect == SIDE_EFFECT_NON_IDEMPOTENT
            else REPLAY_VERIFY_FIRST
        )
        replay = explicit_replay if explicit_replay in {
            REPLAY_VERIFY_FIRST, REPLAY_SAFE_RETRY, REPLAY_DO_NOT_RETRY,
        } else default_replay
        return {"side_effect_class": effect, "replay_policy": replay}

    def _set_side_effect_status(self, action: Optional[Action], status: str,
                                 reason: str = "") -> Dict[str, str]:
        contract = self.side_effect_contract(action)
        if self._task_state is not None:
            self._task_state.side_effect_status = str(status or "UNKNOWN")[:48]
            self._task_state.side_effect_class = contract["side_effect_class"]
            self._task_state.side_effect_replay_policy = contract["replay_policy"]
            self._task_state.side_effect_reason = self._continuity_text(
                reason, 280)
        return contract

    def handoff_to_user(self, reason: str,
                        action: Optional[Action] = None) -> Dict[str, Any]:
        """Stop before replay and expose one bounded existing user handoff."""
        task = self._task_state
        if task is None:
            return {}
        action = action or task.last_action
        contract = self._set_side_effect_status(
            action, "HANDOFF_REQUIRED", reason)
        task.failure_reason = self._continuity_text(reason, 280)
        handoff = {
            "state": "PENDING_USER",
            "task_id": task.task_id,
            "task": task.goal,
            "action_type": str(getattr(action, "action_type", "") or "")[:80],
            "step": str(getattr(task.current_subgoal(), "description", "")
                        if task.current_subgoal() is not None else "")[:240],
            "side_effect_class": contract["side_effect_class"],
            "replay_policy": contract["replay_policy"],
            "reason": self._continuity_text(reason, 280),
            "message": (
                "Task paused before a possibly repeated side effect. "
                "Please review the current application state."
            ),
            "provenance": "TaskEngine+AgentState+user_handoff",
        }
        recorder = getattr(self.agent_state, "record_task_handoff", None)
        previous = None
        getter = getattr(self.agent_state, "task_handoff", None)
        if callable(getter):
            try:
                previous = getter()
            except Exception:
                previous = None
        same_handoff = bool(
            previous
            and previous.get("task_id") == handoff["task_id"]
            and previous.get("action_type") == handoff["action_type"]
            and previous.get("reason") == handoff["reason"]
        )
        if callable(recorder):
            try:
                handoff = recorder(handoff) or handoff
            except Exception:
                log.warning("task handoff recording failed", exc_info=True)
        self.pause_for_runtime_health("verification", reason)
        if callable(self.handoff_notifier) and not same_handoff:
            try:
                self.handoff_notifier(dict(handoff))
            except Exception:
                log.warning("task handoff notification failed", exc_info=True)
        return handoff

    def resolve_user_handoff(self, decision: str) -> Dict[str, Any]:
        """Resolve the current side-effect handoff with at most one retry."""
        task = self._task_state
        handoff = None
        getter = getattr(self.agent_state, "task_handoff", None)
        if callable(getter):
            try:
                handoff = getter()
            except Exception:
                handoff = None
        if task is None or not handoff:
            return {"state": "NO_HANDOFF", "allowed": False}
        choice = str(decision or "").strip().lower()
        if choice in {"stop", "cancel", "no"}:
            task.side_effect_status = "HANDOFF_STOPPED"
            task.side_effect_reason = "user stopped before side-effect replay"
            recorder = getattr(self.agent_state, "record_task_handoff", None)
            if callable(recorder):
                updated = dict(handoff)
                updated["state"] = "STOPPED"
                updated["reason"] = task.side_effect_reason
                recorder(updated)
            self.request_cancel()
            return {"state": "STOPPED", "retry_authorized": False}
        if choice not in {"retry_once", "retry", "continue", "yes"}:
            return {"state": "PENDING_USER", "retry_authorized": False}
        if task.side_effect_retry_count >= 1:
            task.side_effect_status = "HANDOFF_STOPPED"
            task.side_effect_reason = "one explicit side-effect retry already used"
            recorder = getattr(self.agent_state, "record_task_handoff", None)
            if callable(recorder):
                updated = dict(handoff)
                updated["state"] = "STOPPED"
                updated["reason"] = task.side_effect_reason
                recorder(updated)
            self.request_cancel()
            return {"state": "STOPPED", "retry_authorized": False}
        task.side_effect_retry_count = 1
        task.side_effect_status = "RETRY_ALLOWED"
        task.side_effect_reason = "user authorized one side-effect retry"
        recorder = getattr(self.agent_state, "record_task_handoff", None)
        if callable(recorder):
            updated = dict(handoff)
            updated["state"] = "RETRY_AUTHORIZED"
            updated["reason"] = task.side_effect_reason
            recorder(updated)
        event = self._runtime_handoff_event
        self._runtime_handoff_event = None
        self._runtime_handoff_capability = ""
        self._runtime_handoff_loop = None
        self._paused = False
        if event is not None:
            event.set()
        return {"state": "RETRY_AUTHORIZED", "retry_authorized": True}

    def _set_action_fence(self, decision: ActionFenceDecision) -> None:
        self._action_fence = decision
        recorder = getattr(self.agent_state, "record_action_fence", None)
        if callable(recorder):
            try:
                recorder(decision.to_dict())
            except Exception:
                log.warning("action fence evidence recording failed", exc_info=True)

    def _fence_block(
        self,
        *,
        state: str,
        decision: str,
        reason: str,
        action: Action,
        required: tuple[str, ...],
        verification_basis: str = "none",
        blocking_capability: str = "",
        now: Optional[float] = None,
    ) -> ActionFenceDecision:
        result = ActionFenceDecision(
            state=state,
            decision=decision,
            allowed=False,
            reason=self._continuity_text(reason, 280),
            required_capabilities=required,
            blocking_capability=self._continuity_text(blocking_capability, 64),
            task_step=self._continuity_text(
                getattr(getattr(self._task_state, "current_subgoal", lambda: None)(),
                    "description", "") if self._task_state is not None else "",
                240),
            verification_basis=self._continuity_text(
                verification_basis, 160),
            provenance="TaskEngine+CapabilityBus+AgentState",
            checked_at=float(time.time() if now is None else now),
        )
        self._set_action_fence(result)
        return result

    def _fence_allow(
        self,
        *,
        action: Action,
        required: tuple[str, ...],
        verification_basis: str,
        now: Optional[float] = None,
    ) -> ActionFenceDecision:
        result = ActionFenceDecision(
            state=ACTION_FENCE_RELEASED,
            decision=ACTION_ALLOW,
            allowed=True,
            reason="fresh runtime evidence and coherent task continuation",
            required_capabilities=required,
            blocking_capability="",
            task_step=self._continuity_text(
                getattr(getattr(self._task_state, "current_subgoal", lambda: None)(),
                    "description", "") if self._task_state is not None else "",
                240),
            verification_basis=self._continuity_text(
                verification_basis, 160),
            provenance="TaskEngine+CapabilityBus+AgentState",
            checked_at=float(time.time() if now is None else now),
        )
        self._set_action_fence(result)
        return result

    def check_action_fence(self, action: Action,
                           now: Optional[float] = None) -> ActionFenceDecision:
        """Return whether this exact planned action may cross into execution."""
        if not self._action_fence_enabled or self.cap_bus is None:
            result = ActionFenceDecision(
                state=ACTION_FENCE_OPEN,
                decision=ACTION_ALLOW,
                allowed=True,
                reason="action fence is not configured on this isolated harness",
                required_capabilities=(),
                blocking_capability="",
                task_step="",
                verification_basis="not configured",
                provenance="TaskEngine+ActionFence",
                checked_at=float(time.time() if now is None else now),
            )
            self._set_action_fence(result)
            return result
        required = self._action_capabilities(action)
        task = self._task_state
        if task is None:
            return self._fence_block(
                state=ACTION_FENCE_BLOCKED_VERIFICATION,
                decision=ACTION_BLOCK_VERIFICATION,
                reason="TaskEngine has no authoritative task state",
                action=action, required=required, now=now)
        if getattr(action, "step_id", None) != task.current_step:
            return self._fence_block(
                state=ACTION_FENCE_BLOCKED_VERIFICATION,
                decision=ACTION_BLOCK_VERIFICATION,
                reason="action step does not match authoritative current_step",
                action=action, required=required, now=now)
        handoff_getter = getattr(self.agent_state, "task_handoff", None)
        current_handoff = None
        if callable(handoff_getter):
            try:
                current_handoff = handoff_getter()
            except Exception:
                current_handoff = None
        if self.runtime_handoff_pending:
            decision = (
                ACTION_HANDOFF_REQUIRED
                if current_handoff and current_handoff.get("state") == "PENDING_USER"
                else ACTION_BLOCK_RECOVERY
            )
            reason = (
                current_handoff.get("reason") or
                "user handoff is waiting for review"
                if current_handoff
                else "runtime recovery handoff is still pending"
            )
            return self._fence_block(
                state=ACTION_FENCE_BLOCKED_RECOVERY,
                decision=decision,
                reason=reason,
                action=action, required=required,
                blocking_capability=self.runtime_handoff_capability, now=now)
        continuity = None
        getter = getattr(self.agent_state, "task_continuity", None)
        if callable(getter):
            try:
                continuity = getter()
            except Exception:
                continuity = None
        if continuity and continuity.get("resume_decision") in {
                "RESUME_UNSAFE", "HANDOFF_REQUIRED", "ESCALATE"}:
            return self._fence_block(
                state=ACTION_FENCE_BLOCKED_VERIFICATION,
                decision=continuity.get("resume_decision"),
                reason=continuity.get("reason") or
                    "task continuation evidence is not safe",
                action=action, required=required,
                blocking_capability=continuity.get("capability", ""), now=now)
        last_result = task.last_action_result
        if (last_result is not None and
                getattr(last_result, "action", None) is action):
            verified = bool(getattr(task.verification_result, "success", False))
            if verified:
                return self._fence_block(
                    state=ACTION_FENCE_BLOCKED_VERIFICATION,
                    decision=ACTION_HANDOFF_REQUIRED,
                    reason="the current action already has successful verification",
                    action=action, required=required,
                    verification_basis="existing TaskState verification",
                    now=now)
            contract = self.side_effect_contract(action)
            safe_retry = (
                task.side_effect_status == "RETRY_ALLOWED"
                and (contract["replay_policy"] == REPLAY_SAFE_RETRY
                     or task.side_effect_retry_count > 0)
            )
            if not safe_retry:
                return self._fence_block(
                    state=ACTION_FENCE_BLOCKED_VERIFICATION,
                    decision=ACTION_BLOCK_VERIFICATION,
                    reason="current action has an unverified or failed outcome",
                    action=action, required=required,
                    verification_basis="TaskState action result is not verified",
                    now=now)
        current_subgoal = task.current_subgoal()
        if current_subgoal is not None and bool(
                getattr(current_subgoal, "completed", False)):
            return self._fence_block(
                state=ACTION_FENCE_BLOCKED_VERIFICATION,
                decision=ACTION_HANDOFF_REQUIRED,
                reason="current step is already marked completed",
                action=action, required=required,
                verification_basis="TaskState completed step",
                now=now)
        if int(getattr(action, "step_id", 0) or 0) > 0:
            completed = list(getattr(task, "completed_steps", []) or [])
            if len(completed) < int(action.step_id) or any(
                    not bool(getattr(item, "success", False))
                    for item in completed[:int(action.step_id)]):
                return self._fence_block(
                    state=ACTION_FENCE_BLOCKED_VERIFICATION,
                    decision=ACTION_BLOCK_VERIFICATION,
                    reason="previous step evidence is incomplete",
                    action=action, required=required,
                    verification_basis="TaskState completed_steps",
                    now=now)
        try:
            response = self.cap_bus.capability_status({})
        except Exception as exc:
            return self._fence_block(
                state=ACTION_FENCE_BLOCKED_RUNTIME,
                decision=ACTION_BLOCK_RUNTIME,
                reason=f"CapabilityBus freshness read failed: {exc}",
                action=action, required=required, now=now)
        if not response.get("success"):
            return self._fence_block(
                state=ACTION_FENCE_BLOCKED_RUNTIME,
                decision=ACTION_BLOCK_RUNTIME,
                reason=str(response.get("error") or
                           "CapabilityBus returned no authoritative truth"),
                action=action, required=required, now=now)
        records = {
            str(item.get("name", "")): item
            for item in ((response.get("data") or {}).get("capabilities") or [])
            if item.get("name")
        }
        for capability in required:
            record = records.get(capability)
            state = str((record or {}).get("state", "UNAVAILABLE")).upper()
            healthy = (
                state in {"READY", "ACTIVE"}
                or (bool((record or {}).get("ready", False))
                    and state in {"INACTIVE", "WIRED", "PRESENT"})
            )
            if not healthy or bool((record or {}).get("failed", False)):
                return self._fence_block(
                    state=ACTION_FENCE_BLOCKED_RUNTIME,
                    decision=ACTION_BLOCK_RUNTIME,
                    reason=f"required capability {capability} is not freshly healthy",
                    action=action, required=required,
                    blocking_capability=capability, now=now)
        verification_basis = "first action; no prior step required"
        if int(getattr(action, "step_id", 0) or 0) > 0:
            verification_basis = "TaskState completed_steps"
        elif bool(getattr(task.verification_result, "success", False)):
            verification_basis = "existing TaskState verification"
        return self._fence_allow(
            action=action, required=required,
            verification_basis=verification_basis, now=now)

    def _signal_runtime_handoff(self) -> None:
        event = self._runtime_handoff_event
        if event is None:
            return
        loop = self._runtime_handoff_loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(event.set)
                return
            except RuntimeError:
                pass
        event.set()

    def request_cancel(self) -> None:
        """Ask the running task to stop ("stop").

        Takes effect at the next step boundary: no new actions start,
        applications are left intact, and the cancellation is recorded.
        """
        if self._task_state:
            self._task_state.cancel_requested = True
        if self._runtime_handoff_event is not None:
            self._signal_runtime_handoff()

    def pause(self) -> None:
        """Pause after the current step, preserving all task state."""
        self._paused = True

    @staticmethod
    def _continuity_text(value: Any, limit: int) -> str:
        return str(value or "").replace("\n", " ").strip()[:limit]

    @property
    def runtime_handoff_pending(self) -> bool:
        return self._runtime_handoff_event is not None

    @property
    def runtime_handoff_capability(self) -> str:
        return self._runtime_handoff_capability

    def runtime_continuity_snapshot(self) -> Dict[str, Any]:
        """Project current TaskState into bounded handoff evidence.

        TaskState remains authoritative. This method never creates or owns
        task state, plan state, verification, or recovery history.
        """
        task = self._task_state
        if task is None:
            return {}
        subgoal = task.current_subgoal()
        verification = task.verification_result
        last_result = task.last_action_result
        verified = bool(getattr(verification, "success", False))
        if last_result is not None and verified:
            action_status = "VERIFIED"
        elif last_result is not None and bool(getattr(last_result, "success", False)):
            action_status = "EXECUTED_UNVERIFIED"
        elif last_result is not None:
            action_status = "FAILED"
        else:
            action_status = "NONE"
        window = task.current_window if isinstance(task.current_window, dict) else {}
        focus = str(window.get("focused_name") or window.get("focus") or "")
        step = ""
        if subgoal is not None:
            step = str(getattr(subgoal, "description", "") or "")
        if not step and task.last_action is not None:
            step = str(getattr(task.last_action, "target_description", "") or "")
        return {
            "goal": self._continuity_text(task.goal, 280),
            "task_id": self._continuity_text(task.task_id, 96),
            "task": self._continuity_text(
                task.goal or getattr(task.last_action, "target_description", ""),
                280),
            "step": self._continuity_text(step, 240),
            "step_index": max(0, int(task.current_step or 0)),
            "step_total": max(0, len(task.subgoals or [])),
            "application": self._continuity_text(
                task.current_application, 120),
            "window": {
                key: window.get(key)
                for key in ("app", "title", "hwnd", "focused_name", "focus")
                if key in window
            },
            "focus": self._continuity_text(focus, 160),
            "last_verified_progress": {
                "verified": verified,
                "method": self._continuity_text(
                    getattr(verification, "method", ""), 80),
                "evidence": self._continuity_text(
                    getattr(verification, "evidence", ""), 240),
                "action_status": action_status,
            },
            "last_action_step_id": getattr(
                getattr(last_result, "action", None), "step_id", None),
            "task_state": self._state.value.upper(),
            "active": bool(self._running),
            "paused": bool(self._paused),
        }

    def runtime_capability_affects_task(self, capability: str) -> bool:
        """Return deterministic task/capability relevance from current state."""
        task = self._task_state
        if task is None or not self._running:
            return False
        name = str(capability or "").lower()
        phase = self._state.value.upper()
        if name in {"stt", "tts", "hand", "memory", "procedures",
                    "skill_packs", "shared_workspace"}:
            return False
        if phase in {"LISTENING", "SPEAKING", "DONE", "FAILED", "IDLE"}:
            return False
        if name == "recovery":
            return phase == "RECOVERING"
        if name == "brain_routing":
            return phase in {"UNDERSTANDING", "PLANNING"}
        if name in {"perception", "uia", "vision", "computer_tools",
                    "verification"}:
            return phase in {
                "UNDERSTANDING", "OBSERVING", "PLANNING", "ACTING",
                "VERIFYING", "RECOVERING",
            }
        if name == "ocr":
            method = str(getattr(task.verification_result, "method", "") or "")
            subgoal = task.current_subgoal()
            method = method or str(
                getattr(getattr(subgoal, "verification_method", None),
                        "value", "") or "")
            return phase in {"OBSERVING", "ACTING", "VERIFYING"} and (
                "OCR" in method.upper() or phase == "VERIFYING")
        return False

    def pause_for_runtime_health(self, capability: str,
                                 reason: str) -> Optional[Dict[str, Any]]:
        """Hold the existing resume loop at a safe boundary for recovery."""
        # Allow disabling runtime health checks via config for testing
        if get_config().get("orchestrator", "disable_runtime_health_checks", default=False):
            return None
        if not self.runtime_capability_affects_task(capability):
            return None
        if self.runtime_handoff_pending:
            recorder = getattr(self.agent_state, "task_continuity", None)
            if callable(recorder):
                existing = recorder()
                if existing is not None:
                    return existing
            return self.runtime_continuity_snapshot()
        self._runtime_handoff_capability = str(capability or "")[:64]
        self._runtime_handoff_event = asyncio.Event()
        self._paused = True
        context = self.runtime_continuity_snapshot()
        recorder = getattr(self.agent_state, "capture_task_continuity", None)
        if callable(recorder):
            try:
                context = recorder(
                    context,
                    capability=self._runtime_handoff_capability,
                    reason=reason,
                    active_recovery_state="WAITING_FOR_RUNTIME_RECOVERY",
                    interruption_state="WAITING_FOR_RECOVERY",
                    safe_to_resume=False,
                    resume_decision="HANDOFF_REQUIRED",
                ) or context
            except Exception:
                log.warning("task continuity capture failed", exc_info=True)
        return context

    def evaluate_runtime_handoff(self, capability: str,
                                 fresh_status: Dict[str, Any],
                                 recovery_result: Optional[Dict[str, Any]] = None
                                 ) -> Dict[str, Any]:
        """Evaluate task evidence separately from capability recovery."""
        context = self.runtime_continuity_snapshot()
        status_state = str((fresh_status or {}).get("state", "")).upper()
        fresh_ready = status_state in {"READY", "ACTIVE"} and not bool(
            (fresh_status or {}).get("failed", False))
        recovery_outcome = str((recovery_result or {}).get("outcome", "")).upper()
        if not fresh_ready:
            decision = "ESCALATE"
            reason = "required capability is not freshly READY/ACTIVE"
        elif recovery_outcome and recovery_outcome not in {
                "RECOVERED", "NO_OP"}:
            decision = "ESCALATE"
            reason = "existing capability recovery did not verify success"
        else:
            phase = str(context.get("task_state", ""))
            progress = context.get("last_verified_progress") or {}
            action_status = str(progress.get("action_status", "NONE"))
            last_step = context.get("last_action_step_id")
            current_step = context.get("step_index")
            if (bool(progress.get("verified")) and
                    last_step is not None and current_step is not None and
                    int(last_step) == int(current_step)):
                decision = "HANDOFF_REQUIRED"
                reason = "current action already has successful verification; do not replay"
            elif phase in {"ACTING", "VERIFYING"} and action_status != "VERIFIED":
                decision = "RESUME_UNSAFE"
                reason = "current action has no successful task verification"
            elif action_status in {"EXECUTED_UNVERIFIED", "FAILED"}:
                decision = "RESUME_UNSAFE"
                reason = "last task action is partial or failed"
            elif (phase in {"OBSERVING", "PLANNING", "UNDERSTANDING"} and
                  not context.get("step")):
                decision = "HANDOFF_REQUIRED"
                reason = "continuation point is not known"
            elif (phase in {"OBSERVING", "PLANNING", "VERIFYING", "ACTING"} and
                  action_status == "VERIFIED"):
                decision = "RESUME_SAFE"
                reason = "task evidence verifies the current continuation point"
            elif phase in {"OBSERVING", "PLANNING", "UNDERSTANDING"}:
                decision = "RESUME_SAFE"
                reason = "no unverified action is in flight"
            else:
                decision = "HANDOFF_REQUIRED"
                reason = "TaskEngine requires an explicit continuation boundary"
        return {
            "capability": str(capability or "")[:64],
            "decision": decision,
            "safe_to_resume": decision == "RESUME_SAFE",
            "reason": reason,
            "fresh_capability_state": status_state,
            "recovery_outcome": recovery_outcome,
            "task_state": context.get("task_state", ""),
            "resumed_step": context.get("step", "") if decision == "RESUME_SAFE" else "",
            "verification_result": context.get("last_verified_progress", {}),
            "provenance": "TaskEngine+AgentState+RuntimeHealthSupervisor",
        }

    def complete_runtime_handoff(self, evaluation: Dict[str, Any]
                                 ) -> Dict[str, Any]:
        """Release or retain the existing TaskEngine handoff gate."""
        decision = str((evaluation or {}).get("decision", "HANDOFF_REQUIRED"))
        context = self.runtime_continuity_snapshot()
        recorder = getattr(self.agent_state, "capture_task_continuity", None)
        if callable(recorder):
            try:
                context = recorder(
                    context,
                    capability=(evaluation or {}).get(
                        "capability", self._runtime_handoff_capability),
                    reason=(evaluation or {}).get("reason", ""),
                    active_recovery_state=decision,
                    interruption_state=decision,
                    safe_to_resume=bool(
                        (evaluation or {}).get("safe_to_resume", False)),
                    resume_decision=decision,
                ) or context
            except Exception:
                log.warning("task continuity update failed", exc_info=True)
        result = dict(evaluation or {})
        if decision == "RESUME_SAFE" and self._runtime_handoff_event is not None:
            self._signal_runtime_handoff()
            self._runtime_handoff_event = None
            self._runtime_handoff_capability = ""
            self._runtime_handoff_loop = None
            self._paused = False
            result["handoff_state"] = "RESUMED"
        else:
            result["handoff_state"] = decision
        result["resumed_step"] = (
            self.runtime_continuity_snapshot().get("step", "")
            if decision == "RESUME_SAFE" else "")
        result["final_task_state"] = self._state.value.upper()
        return result

    async def resume(self) -> TaskState:
        """Continue a paused task (or run a fresh one) from its current
        state. Never restarts completed work: steps advance monotonically
        through current_step."""
        self._initialize_task_bounds(self._task_state)
        if self._runtime_handoff_event is None:
            self._paused = False
        else:
            self._runtime_handoff_loop = asyncio.get_running_loop()
        while (self._running
               and self._state not in (OrchestratorState.DONE,
                                       OrchestratorState.FAILED)):
            event = self._runtime_handoff_event
            if event is not None:
                await event.wait()
                if (self._runtime_handoff_event is event and
                        self._task_state is not None and
                        self._task_state.cancel_requested):
                    self._runtime_handoff_event = None
                if self._runtime_handoff_event is event:
                    continue
                self._paused = False
            elif self._paused:
                break
            if (self._task_state is not None
                    and self._task_state.cancel_requested):
                self._task_state.cancelled = True
                self._task_state.failure_reason = "cancelled by user"
                self._task_state.recovery_history.append({
                    "phase": "cancelled",
                    "timestamp": time.time(),
                })
                self._running = False
                self._transition(OrchestratorState.IDLE)
                break

            task = self._task_state
            metrics = task.metrics if task is not None else {}
            iteration_limit = max(1, int(metrics.get(
                "max_iterations", self.max_iterations) or 1))
            iterations = int(metrics.get("iterations", 0) or 0)
            if iterations >= iteration_limit:
                self._fail_bounded(
                    f"maximum task iteration limit reached ({iteration_limit})")
                break
            metrics["iterations"] = iterations + 1
            before_progress = self._progress_marker(task)
            await self._step()

            # A state transition alone is not task progress. Count repeated
            # no-op iterations so a blocked fence, stale planner, or recovery
            # path that keeps returning to the same point cannot spin forever.
            if (self._state not in (OrchestratorState.DONE,
                                    OrchestratorState.FAILED,
                                    OrchestratorState.IDLE)
                    and task is not None):
                after_progress = self._progress_marker(task)
                if before_progress == after_progress:
                    streak = int(metrics.get("no_progress_streak", 0) or 0) + 1
                else:
                    streak = 0
                metrics["no_progress_streak"] = streak
                no_progress_limit = max(1, int(metrics.get(
                    "no_progress_limit", self.no_progress_limit) or 1))
                if streak >= no_progress_limit:
                    self._fail_bounded(
                        "no progress: task state and outcome did not change "
                        f"for {streak} iterations")
                    break

        # Handle terminal state cleanup (transitions to IDLE)
        if self._state in (OrchestratorState.DONE, OrchestratorState.FAILED):
            await self._do_terminal()

        return self._task_state

    async def repair_capability(
        self,
        capability: str,
        *,
        capability_bus: Optional[Any] = None,
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        """Diagnose and perform one approved low-risk owner repair.

        CapabilityBus supplies fresh truth before and after the operation.
        TaskEngine is the sole caller of the existing owner methods; this
        method never creates owners, changes configuration, or persists data.
        """
        bus = capability_bus or getattr(self, "cap_bus", None)

        def fresh_status():
            if bus is None:
                return None, "CapabilityBus unavailable"
            try:
                response = bus.capability_status({})
            except Exception as exc:
                return None, f"{type(exc).__name__}: {exc}"
            if not response.get("success"):
                return None, str(response.get("error") or "status unavailable")
            for record in ((response.get("data") or {}).get("capabilities") or []):
                if record.get("name") == capability:
                    return record, ""
            return None, "capability status record not found"

        status, _ = fresh_status()
        if bus is None:
            decision = propose_repair(capability, None, None)
            decision.post_repair_status = {
                "state": "UNAVAILABLE",
                "reason": "CapabilityBus unavailable",
            }
            decision.execution_result = {
                "attempted": False,
                "success": False,
                "reason": "CapabilityBus unavailable",
            }
            decision.verification_result = {
                "success": False,
                "method": "fresh_capability_status",
                "reason": "repair could not be attempted without CapabilityBus",
            }
            return decision.to_dict()

        decision = propose_repair(capability, status, bus)
        if not decision.approved:
            post_status, post_error = fresh_status()
            decision.post_repair_status = post_status or {
                "state": "UNAVAILABLE",
                "reason": post_error or "status unavailable",
            }
            healthy = post_status and post_status.get("state") in ("READY", "ACTIVE")
            decision.execution_result = {
                "attempted": False,
                "success": bool(healthy),
                "reason": ("already healthy; no repair needed" if healthy else
                           "no approved low-risk repair was available"),
            }
            decision.verification_result = {
                "success": bool(healthy),
                "method": "fresh_capability_status",
                "reason": ("capability is healthy without repair" if healthy else
                           "capability remains unhealthy or unverifiable"),
            }
            return decision.to_dict()

        action = decision.proposed_repair
        owner = {
            "ear.warm_start": getattr(bus, "ear", None),
            "perception_service.start": getattr(bus, "perception_service", None),
            "screentree.refresh_sync": getattr(bus, "screentree", None),
        }.get(action)
        if owner is None or not callable(getattr(owner, action.rsplit(".", 1)[-1], None)):
            decision.approved = False
            decision.repairable = False
            decision.risk = "UNSAFE_TO_REPAIR"
            decision.execution_result = {
                "attempted": False,
                "success": False,
                "reason": "approved owner operation became unavailable",
            }
            decision.verification_result = {
                "success": False,
                "method": "fresh_capability_status",
                "reason": "repair owner was not available at execution time",
            }
            return decision.to_dict()

        attempts_limit = max(1, min(int(max_attempts or 1), 2))
        last_error = ""
        for attempt in range(1, attempts_limit + 1):
            try:
                result = getattr(owner, action.rsplit(".", 1)[-1])()
                if inspect.isawaitable(result):
                    result = await result
                action_success = (result is not False if
                                  action == "perception_service.start"
                                  else bool(result))
                if not action_success:
                    last_error = "owner operation returned false"
            except Exception as exc:
                action_success = False
                last_error = f"{type(exc).__name__}: {exc}"

            post_status, post_error = fresh_status()
            post_status = post_status or {
                "state": "UNAVAILABLE",
                "reason": post_error or "status unavailable",
            }
            ready = bool(post_status.get("state") in ("READY", "ACTIVE") and
                         post_status.get("ready"))
            if action_success and ready:
                decision.attempts = attempt
                decision.post_repair_status = post_status
                decision.execution_result = {
                    "attempted": True,
                    "success": True,
                    "attempts": attempt,
                    "action": action,
                }
                decision.verification_result = {
                    "success": True,
                    "method": "fresh_capability_status",
                    "reason": "owner operation completed and fresh status is ready",
                }
                return decision.to_dict()
            if post_error:
                last_error = last_error or post_error

        post_status, post_error = fresh_status()
        decision.attempts = attempts_limit
        decision.post_repair_status = post_status or {
            "state": "UNAVAILABLE",
            "reason": post_error or "status unavailable",
        }
        decision.execution_result = {
            "attempted": True,
            "success": False,
            "attempts": attempts_limit,
            "action": action,
            "reason": last_error or "repair did not restore readiness",
        }
        decision.verification_result = {
            "success": False,
            "method": "fresh_capability_status",
            "reason": "repair completed without fresh READY/ACTIVE status",
        }
        return decision.to_dict()

    async def recover_capability(
        self,
        capability: str,
        problem: str = "",
        *,
        capability_bus: Optional[Any] = None,
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        """Run one bounded capability recovery and record its outcome.

        This is an orchestration wrapper around repair_capability(); it does
        not add a retry loop or a second repair executor. Existing AgentState
        receives only a compact in-memory recovery note when available.
        """
        repair = await self.repair_capability(
            capability,
            capability_bus=capability_bus,
            max_attempts=max_attempts,
        )
        execution = repair.get("execution_result") or {}
        verification = repair.get("verification_result") or {}
        post_status = repair.get("post_repair_status") or {}
        state = str(post_status.get("state", "UNAVAILABLE")).upper()
        verified = bool(verification.get("success"))
        if verified and not execution.get("attempted"):
            outcome = "NO_OP"
        elif verified:
            outcome = "RECOVERED"
        elif state == "FAILED":
            outcome = "FAILED"
        elif state == "DEGRADED":
            outcome = "DEGRADED"
        else:
            outcome = "UNREPAIRED"

        detected_problem = str(problem or repair.get("problem") or "")[:280]
        result = {
            "capability": repair.get("capability", str(capability)[:64]),
            "problem": detected_problem,
            "diagnosis": repair.get("diagnosis", ""),
            "outcome": outcome,
            "repair": repair,
            "post_repair_status": post_status,
            "verification_result": verification,
            "attempts": int(repair.get("attempts", 0) or 0),
            "provenance": repair.get("provenance", ""),
            "recovery_recorded": False,
        }

        agent_state = getattr(self, "agent_state", None)
        if agent_state is not None and callable(
                getattr(agent_state, "set_recovery", None)):
            note = (
                f"{result['capability']}: {outcome}; "
                f"{result['diagnosis']}; attempts={result['attempts']}"
            )
            try:
                agent_state.set_recovery(note[:240], result["attempts"])
                result["recovery_recorded"] = True
            except Exception:
                # Recovery truth is already complete; recording is best effort.
                pass
        return result

    async def _step(self) -> None:
        """Execute one step of the state machine."""
        import time as _time
        _started = _time.monotonic()
        try:
            if self._state == OrchestratorState.LISTENING:
                await self._do_listening()
            elif self._state == OrchestratorState.UNDERSTANDING:
                await self._do_understanding()
            elif self._state == OrchestratorState.OBSERVING:
                await self._do_observing()
            elif self._state == OrchestratorState.PLANNING:
                await self._do_planning()
            elif self._state == OrchestratorState.ACTING:
                await self._do_acting()
            elif self._state == OrchestratorState.VERIFYING:
                await self._do_verifying()
            elif self._state == OrchestratorState.RECOVERING:
                await self._do_recovering()
            elif self._state == OrchestratorState.SPEAKING:
                await self._do_speaking()
            elif self._state == OrchestratorState.INTERRUPTED:
                await self._do_interrupted()
            elif self._state in (OrchestratorState.DONE, OrchestratorState.FAILED):
                await self._do_terminal()
            else:
                log.warning(f"No handler for state {self._state}")
                self._transition(OrchestratorState.FAILED)
        except TransitionError:
            raise
        except Exception as e:
            log.exception(f"Error in state {self._state}: {e}")
            self._task_state.failure_reason = str(e)
            self._transition(OrchestratorState.FAILED)
        finally:
            # Phase 5 efficiency metrics: per-state time + step count.
            # Never fails the task (best effort).
            try:
                import time as _time
                if self._task_state is not None:
                    metrics = self._task_state.metrics
                    key = f"seconds_in_{self._state.value}"
                    metrics[key] = metrics.get(key, 0.0) + (
                        _time.monotonic() - _started)
                    metrics["steps"] = metrics.get("steps", 0) + 1
            except Exception:
                pass
    
    # State handlers

    def _observe_for_task(self, required_level, force_refresh=True):
        """Observe the right window for this task's execution mode.

        Foreground tasks observe the foreground (existing behavior).
        Background tasks observe their pinned window WITHOUT focusing it
        (existing behavior would steal the user's foreground on every
        state). Returns a PerceptionSnapshot either way.
        """
        try:
            if (self._task_state is not None
                    and getattr(self._task_state, "execution_mode",
                                "foreground") == "background"
                    and getattr(self._task_state, "target_hwnd", None)):
                _snap = self.perception.observe_window(
                    self._task_state.target_hwnd, required_level)
                self._publish_visual_state(_snap, "task_engine:background")
                return _snap
        except Exception as e:
            log.warning(f"Pinned-window observe failed: {e}")
        _snap = self.perception.observe(required_level,
                                        force_refresh=force_refresh)
        self._publish_visual_state(_snap, "task_engine")
        return _snap

    def _publish_visual_state(self, snapshot, source: str = "") -> None:
        """Mirror one observation onto the Visual State Bus (best effort)."""
        try:
            if self.visual_state_bus is None or snapshot is None:
                return
            _goal = ""
            try:
                if self._task_state is not None:
                    _goal = self._task_state.goal or ""
            except Exception:
                pass
            self.visual_state_bus.publish(
                snapshot, source=source, goal=_goal)
        except Exception as e:
            log.warning(f"visual-state publish failed: {e}")

    def current_visual_state(self):
        """Current bus state for grounding/verification callers (or None)."""
        try:
            if self.visual_state_bus is None:
                return None
            return self.visual_state_bus.get()
        except Exception:
            return None

    async def _do_listening(self) -> None:
        """Wait for user input (handled externally via handle_user_input)."""
        # In practice, this waits for the voice pipeline to deliver text
        pass
    
    async def _do_understanding(self) -> None:
        """Parse intent, retrieve relevant memory, classify goal, and route via IntelligenceRouter."""
        if not self._task_state or not self._task_state.user_interrupt:
            self._transition(OrchestratorState.FAILED)
            return
        
        user_text = self._task_state.user_interrupt
        self._task_state.voice_state = VoiceState.THINKING
        
        # Retrieve relevant memory
        if self.memory:
            self._task_state.relevant_memory = self._retrieve_memory(user_text)
        
        # Phase 2B: Use IntelligenceRouter if available
        if self.intelligence_router:
            # Get fresh perception for routing decision. Level 2 (not just
            # window identity) so the router can plan from the controls that
            # are actually on screen (e.g. which toolbar button to press).
            perception = self.perception.observe(
                PerceptionLevel.LEVEL_2_UIA_TREE,
                force_refresh=True
            )
            self._task_state.last_observation = perception
            
            # Route the task
            routing_result = self.intelligence_router.route(
                task_state=self._task_state,
                perception=perception,
                user_intent=user_text,
            )
            
            self._task_state.recovery_history.append({
                "phase": "routing",
                "decision": routing_result.decision.value,
                "confidence": routing_result.confidence,
                "reason": routing_result.reason,
                "timestamp": time.time(),
            })
            
            # Handle routing decision
            if routing_result.decision == RouteDecision.DETERMINISTIC_SKILL:
                # Use the router's plan which has the correct target_description
                if routing_result.plan:
                    self._task_state.subgoals = routing_result.plan.steps
                else:
                    # Fallback for backward compatibility
                    self._task_state.subgoals = [SubGoal(
                        description=f"Execute {routing_result.skill.name}",
                        intent=user_text,
                        action_type=routing_result.skill.name,
                        target_description=user_text,
                        verification_method=routing_result.skill.verification_methods[0] if routing_result.skill.verification_methods else VerificationMethod.CUSTOM,
                        risk_level=routing_result.skill.risk_level,
                    )]
            elif routing_result.decision == RouteDecision.VERIFIED_PROCEDURE:
                # Verified procedure - use the router's BOUND plan, whose
                # literals were substituted for THIS goal. Rebuilding from
                # the raw stored steps would replay {placeholders} verbatim.
                if routing_result.plan:
                    self._task_state.subgoals = routing_result.plan.steps
                    # Remember which stored procedure this run replays so a
                    # verified completion feeds evidence back to it.
                    try:
                        self._task_state.learned_procedure = \
                            routing_result.procedure
                    except Exception:
                        pass
                else:
                    proc = routing_result.procedure
                    self._task_state.subgoals = []
                    for i, step_data in enumerate(proc.steps):
                        self._task_state.subgoals.append(SubGoal(
                            description=step_data.get("description", ""),
                            intent=step_data.get("intent", ""),
                            action_type=step_data.get("action_type", "execute"),
                            target_description=step_data.get("target", ""),
                            expected_result=step_data.get("expected", ""),
                            verification_method=VerificationMethod.CUSTOM,
                            risk_level=RiskLevel.LOW,
                            order=i,
                        ))
            elif routing_result.decision == RouteDecision.LOCAL_REASONING:
                # Local reasoning produced a plan
                if routing_result.plan:
                    self._task_state.subgoals = routing_result.plan.steps
                    # Partial plans flow: unknown remainder is planned by
                    # the general model path with full goal context, so
                    # known work executes first and discovery continues.
                    for _rem in (getattr(routing_result, "unplanned_texts",
                                         None) or []):
                        _extra = await self._decompose_via_model(
                            user_text, _rem)
                        if _extra:
                            self._task_state.subgoals.extend(_extra)
                        else:
                            self._task_state.subgoals.append(SubGoal(
                                description=f"Continue goal: {_rem[:120]}",
                                intent=user_text,
                                action_type="execute",
                                target_description=_rem[:200],
                                verification_method=VerificationMethod.CUSTOM,
                            ))
            elif routing_result.decision == RouteDecision.MODEL_FALLBACK:
                # Fall back to model-based decomposition
                self._task_state.subgoals = await self._decompose_goal(user_text)
                self._task_state.recovery_history.append({
                    "phase": "fallback",
                    "reason": routing_result.fallback_reason,
                    "timestamp": time.time(),
                })
            else:
                # No named solver: NOT a failure. Unknown work flows into
                # general model planning with the full goal intact; only
                # a planning failure itself may fail the task.
                _model_steps = await self._decompose_via_model(
                    user_text, user_text)
                if _model_steps:
                    self._task_state.subgoals = _model_steps
                    self._task_state.recovery_history.append({
                        "phase": "general-planning",
                        "reason": "no named solver; model planned",
                        "timestamp": time.time(),
                    })
                else:
                    self._task_state.failure_reason = routing_result.reason
                    self._transition(OrchestratorState.FAILED)
                    return

            self._task_state.pending_steps = list(self._task_state.subgoals)

            # Seed task confidence from the routing result. Without this
            # it sits at the 0.0 default, and recovery's confidence gate
            # escalates on the FIRST failure — retries never happen.
            try:
                _rc = float(getattr(routing_result, "confidence", 0.0) or 0.0)
            except Exception:
                _rc = 0.0
            if _rc > 0:
                self._task_state.confidence = _rc

            if not self._task_state.subgoals:
                # Simple query - can answer directly
                await self._answer_directly(user_text)
                self._transition(OrchestratorState.DONE)
            else:
                self._task_state.current_step = 0
                self._transition(OrchestratorState.OBSERVING)
        else:
            # Legacy path - use intelligence backend directly
            self._task_state.subgoals = await self._decompose_goal(user_text)
            self._task_state.pending_steps = list(self._task_state.subgoals)
            
            if not self._task_state.subgoals:
                # Simple query - can answer directly
                await self._answer_directly(user_text)
                self._transition(OrchestratorState.DONE)
            else:
                self._task_state.current_step = 0
                self._transition(OrchestratorState.OBSERVING)
    
    async def _do_observing(self) -> None:
        """Capture fresh perception before acting."""
        self._task_state.voice_state = VoiceState.ACTING
        
        # Get fresh perception (pinned window for background tasks)
        required_level = self._required_perception_level()
        perception = self._observe_for_task(required_level,
                                            force_refresh=True)

        self._task_state.last_observation = perception
        self._task_state.current_application = perception.active_app
        self._task_state.current_window = perception.active_window
        
        # Compute screen delta
        if self._task_state.last_observation and self._task_state.screen_delta is not None:
            self._task_state.screen_delta = self.perception.detect_change(
                self._task_state.last_observation
            )
        
        # Check if perception is fresh enough for acting
        if not perception.is_fresh(self.perception_freshness_seconds):
            log.warning("Perception too stale for acting")
            self._transition(OrchestratorState.FAILED)
            return

        if self._record_observation_probe(perception):
            return
        
        self._transition(OrchestratorState.PLANNING)
    
    async def _do_planning(self) -> None:
        """Select next action for current subgoal."""
        subgoal = self._task_state.current_subgoal()
        if not subgoal:
            self._transition(OrchestratorState.DONE)
            return
        
        # Build action for current subgoal. A semantic hand proposal is only
        # an input to this existing planner boundary; it receives the same
        # permission, fence, executor, and verification path below.
        hand_action = self._pending_hand_action
        self._pending_hand_action = None
        if (hand_action is not None and
                hand_action.task_id == self._task_state.task_id and
                hand_action.step_id == self._task_state.current_step):
            action = hand_action
        elif self._hand_action_is_fresh(hand_action):
            # The step advanced between the pinch and this boundary, so the
            # exact-id check above failed and the click used to be dropped in
            # favour of _plan_action - which needs the brain. With no working
            # provider that re-plan fails and the click never happens. A hand
            # action is coordinate-complete and needs no planning, so honour a
            # recent one instead of discarding it.
            log.info(
                "hand action honoured after step advance: %s (age=%.2fs)",
                getattr(hand_action, "action_type", "?"),
                max(0.0, time.time() - float(
                    (getattr(hand_action, "parameters", {}) or {}).get(
                        "hand_dispatched_at", 0.0) or 0.0)))
            action = hand_action
        else:
            action = await self._plan_action(subgoal)
        self._task_state.last_action = action
        
        # Check permissions
        if self.permission_gate:
            perm_result = self._check_permission(action)
            if perm_result == PermissionState.DENIED:
                self._task_state.permission_state = PermissionState.DENIED
                self._transition(OrchestratorState.FAILED)
                return
            elif perm_result == PermissionState.PENDING:
                self._task_state.permission_state = PermissionState.PENDING
                permission = await self._ask_permission(action)
                if permission != PermissionState.GRANTED:
                    self._task_state.permission_state = PermissionState.DENIED
                    self._task_state.failure_reason = (
                        "permission denied or confirmation timed out")
                    self._transition(OrchestratorState.FAILED)
                    return
                self._task_state.permission_state = PermissionState.GRANTED
            else:
                self._task_state.permission_state = PermissionState.GRANTED
        
        self._transition(OrchestratorState.ACTING)

    async def _ask_permission(self, action: Action) -> PermissionState:
        """Use the existing bounded ConfirmationGate; fail closed."""
        gate = self.permission_gate
        ask = getattr(gate, "ask", None)
        if not callable(ask):
            return PermissionState.DENIED
        question = (
            f"May I {getattr(action, 'action_type', 'perform this action')} "
            f"{getattr(action, 'target_description', '')[:120]}?"
        )
        try:
            result = ask(question)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            log.warning("permission confirmation failed: %s", exc)
            return PermissionState.DENIED
        return PermissionState.GRANTED if bool(result) else PermissionState.DENIED
    
    @staticmethod
    def _obs_title(obs) -> str:
        try:
            w = getattr(obs, "active_window", None) or {}
            return w.get("title", "") if isinstance(w, dict) else ""
        except Exception:
            return ""

    async def _ensure_task_window(self) -> bool:
        """Make sure the task's application still owns the foreground.

        Snapshot-relative guards cannot catch a foreground that moved
        BEFORE the current capture (they compare the new window against
        itself). The task-anchored application recorded after the last
        open can: on mismatch, try once to re-focus the pinned window,
        else report False so the caller recovers instead of acting into
        whatever the user switched to. Read-only when already correct.
        Identity is canonical (see app_instances): UWP-hosted windows
        match by title since the host executable is shared.
        """
        from .app_instances import app_identity_matches
        anchor = (getattr(self._task_state, "target_application", "")
                  or "").strip()
        obs = self._task_state.last_observation
        current_exe = getattr(obs, "active_app", "") if obs else ""
        current_title = self._obs_title(obs)
        if not anchor:
            return True
        if app_identity_matches(anchor, current_exe, current_title):
            return True
        # An open_app step legitimately moves the foreground to its
        # intended app; that is the plan working, not contention.
        try:
            planned = self._task_state.last_action
            if planned is not None and (
                    planned.action_type or "").lower() in (
                    "open_app", "open_application"):
                if app_identity_matches(
                        getattr(planned.target, "text_match", ""),
                        current_exe, current_title):
                    self._task_state.target_application = (
                        planned.target.text_match or
                        self._task_state.target_application)
                    return True
        except Exception:
            pass
        try:
            self._task_state.recovery_history.append({
                "phase": "foreground_contention",
                "expected_app": anchor,
                "observed_app": current,
                "timestamp": time.time(),
            })
        except Exception:
            pass
        hwnd = getattr(self._task_state, "target_hwnd", None)
        if not (isinstance(hwnd, int) and not isinstance(hwnd, bool)):
            return False
        try:
            refocus = Action(
                task_id=self._task_state.task_id,
                step_id=self._task_state.current_step,
                intent=f"refocus {anchor} window",
                action_type="open_app",
                target=TargetSpec(
                    text_match=self._app_from_prior_open() or anchor,
                    hwnd=hwnd),
                target_description=f"refocus {anchor}",
                grounding_method=GroundingMethod.ABSOLUTE_COORDS,
                expected_result=ExpectedResult(),
                verification_method=VerificationMethod.CUSTOM,
                risk_level=RiskLevel.LOW,
            )
            result = await self.action_executor.execute(
                action=refocus,
                perception=obs,
                permission_state=self._task_state.permission_state,
            )
            if not result.success:
                return False
            fresh = self._observe_for_task(
                PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)
            self._task_state.last_observation = fresh
            self._task_state.current_application = fresh.active_app
            self._task_state.current_window = fresh.active_window
            return app_identity_matches(anchor, fresh.active_app,
                                        self._obs_title(fresh))
        except Exception as e:
            log.warning(f"Task window refocus failed: {e}")
            return False

    async def _do_acting(self) -> None:
        """Execute the planned action with grounding and verification."""
        action = self._task_state.last_action
        if not action:
            self._transition(OrchestratorState.FAILED)
            return

        if bool((getattr(action, "parameters", {}) or {}).get(
                "hand_semantic", False)):
            log.info(
                "HANDEXEC act=%s xy=%s desc=%r",
                getattr(action, "action_type", "?"),
                tuple(getattr(getattr(action, "target", None),
                              "coordinates", ()) or ()),
                str(getattr(action, "target_description", ""))[:60])

        # Hand actions are dispatched from the sensor thread, but their
        # observation/execution work belongs to this normal task thread.
        if bool((getattr(action, "parameters", {}) or {}).get(
                "hand_semantic", False)):
            try:
                required_level = self._required_perception_level()
                fresh = self._observe_for_task(required_level,
                                               force_refresh=True)
                if fresh is not None:
                    self._task_state.last_observation = fresh
                    self._task_state.current_application = fresh.active_app
                    self._task_state.current_window = fresh.active_window
            except Exception as exc:
                log.warning("hand action task observation failed: %s", exc)

        # Phase 3: In real execution mode, always capture fresh perception before acting
        if self.use_real_execution:
            required_level = self._required_perception_level()
            perception = self._observe_for_task(required_level,
                                                force_refresh=True)
            self._task_state.last_observation = perception
            self._task_state.current_application = perception.active_app
            self._task_state.current_window = perception.active_window

            # Phase 5: task-app anchoring. Snapshot-relative guards below
            # cannot catch a foreground that moved BEFORE this capture
            # (they would compare the new window against itself). The
            # task's anchored application can: if it no longer matches,
            # try to re-focus the pinned window once, else recover.
            if not await self._ensure_task_window():
                self._task_state.failure_reason = (
                    "foreground left task application; refocus failed")
                # ACTING cannot transition to RECOVERING directly; route
                # through VERIFYING (which fails on the unexecuted step
                # and lands in RECOVERING through the legal edge).
                self._transition(OrchestratorState.VERIFYING)
                return

            # Compute screen delta
            if self._task_state.last_observation and self._task_state.screen_delta is not None:
                self._task_state.screen_delta = self.perception.detect_change(
                    self._task_state.last_observation
                )

        # Verify perception freshness
        if not self._task_state.last_observation or \
           not self._task_state.last_observation.is_fresh(self.perception_freshness_seconds):
            self._task_state.failure_reason = "perception unavailable or stale"
            self._transition(OrchestratorState.FAILED)
            return

        if not self._action_budget_available("planned action"):
            return
        
        fence = self.check_action_fence(action)
        if not fence.allowed:
            self._task_state.failure_reason = fence.reason
            try:
                metrics = self._task_state.metrics
                metrics["action_fence_blocks"] = metrics.get(
                    "action_fence_blocks", 0) + 1
            except Exception:
                pass
            handoff_capability = (
                fence.blocking_capability
                or (fence.required_capabilities[0]
                    if fence.required_capabilities else "verification")
            )
            self.pause_for_runtime_health(
                handoff_capability, fence.reason)
            return

        self._set_side_effect_status(
            action, "IN_FLIGHT", "action crossed the TaskEngine fence")
        # Execute action
        # Reserve the attempt before crossing the executor boundary. This
        # counts failed/unknown executions too, so an executor that repeatedly
        # errors cannot consume an unbounded number of side-effect attempts.
        try:
            self._task_state.metrics["actions"] = (
                self._task_state.metrics.get("actions", 0) + 1)
        except Exception:
            pass
        result = await self.action_executor.execute(
            action=action,
            perception=self._task_state.last_observation,
            permission_state=self._task_state.permission_state,
        )
        
        result_status = str(
            getattr(getattr(result, "status", ""), "value",
                    getattr(result, "status", "")) or "").lower()
        if result_status in {"not_grounded", "permission_denied"}:
            self._set_side_effect_status(
                action, "NOT_STARTED", "action was rejected before side effect")
        elif bool(getattr(result, "success", False)):
            self._set_side_effect_status(
                action, "EXECUTED_UNVERIFIED",
                "action returned success; task verification is still required")
        else:
            self._set_side_effect_status(
                action, "UNKNOWN",
                getattr(result, "error", "") or "action outcome is unknown")

        # Remember where a grounded action landed so verification can
        # focus there (e.g. cropped screen-delta around a click point).
        # The control_name/role grounding record itself is untouched.
        # The coordinates also persist on the task for the keyboard steps
        # that follow a click (their effects appear around the focused
        # control, which has no target of its own).
        if result.grounded_coordinates:
            action.target.coordinates = result.grounded_coordinates
            self._task_state.last_grounded_coordinates = (
                result.grounded_coordinates)

        # Create step result
        subgoal = self._task_state.current_subgoal()
        step_result = StepResult(
            subgoal=subgoal,
            action=action,
            success=result.success,
            actual_result=result.message,
            error=result.error if not result.success else "",
            duration_seconds=result.duration_seconds,
        )
        self._task_state.last_action_result = step_result
        self._publish_hand_action_feedback(
            action, "executed", bool(result.success), verified=False)
        self._task_state.expected_result = action.expected_result
        self._task_state.actual_result = result.actual_result
        try:
            metrics = self._task_state.metrics
            if not result.success:
                metrics["action_failures"] = metrics.get(
                    "action_failures", 0) + 1
        except Exception:
            pass
        # Phase 5 ownership: after a successful open, record which window
        # the task ended up with (current_window carries hwnd/app from
        # perception). New HWNDs become task-owned; the rest stays borrowed.
        try:
            if (result.success and (action.action_type or "").lower() in (
                    "open_app", "open_application")):
                # Anchor to the action's intended app first: current_window
                # here is still the PRE-action perception (e.g. the terminal
                # that launched the task), so preferring it keeps the anchor
                # stuck on the launcher and every step after the open looks
                # like contention. The hwnd is refined after fresh capture
                # in _do_verifying.
                intent = (getattr(action.target, "text_match", "") or "")
                if intent:
                    self._task_state.target_application = intent
                cur = self._task_state.current_window or {}
                hwnd = cur.get("hwnd")
                if isinstance(hwnd, int) and not isinstance(hwnd, bool):
                    # Pin only when the pre-action window already IS the
                    # intended app. Pinning the launcher/contender here
                    # makes every later step misread the task's own
                    # window as contention (and poisons recovery's
                    # refocus target).
                    try:
                        from .app_instances import app_identity_matches
                        _want = intent or ""
                        _cur_app = cur.get("app", "") or ""
                        _cur_title = cur.get("title", "") or ""
                        _matches = (not _want) or app_identity_matches(
                            _want, _cur_app, _cur_title)
                    except Exception:
                        _matches = False
                    if _matches:
                        self._task_state.target_hwnd = hwnd
                        if not intent:
                            self._task_state.target_application = (
                                cur.get("app", "") or "")
        except Exception:
            pass

        self._transition(OrchestratorState.VERIFYING)
    
    async def _do_verifying(self) -> None:
        """Verify action outcome against expected result."""
        self._task_state.voice_state = VoiceState.VERIFYING
        
        # Capture post-action perception (escalate to OCR when the
        # verification method needs visible-text evidence).
        verify_level = PerceptionLevel.LEVEL_2_UIA_TREE  # At minimum
        last_action = self._task_state.last_action
        # Keyboard steps carry no coordinates, but their effects appear
        # around the last grounded interaction point; lend it to them so
        # cropped verification has something truthful to look at.
        if (last_action is not None
                and getattr(last_action.target, 'coordinates', None) is None
                and self._task_state.last_grounded_coordinates is not None):
            last_action.target.coordinates = (
                self._task_state.last_grounded_coordinates)
        if last_action and last_action.verification_method in (
            VerificationMethod.OCR_TEXT_APPEARED,
            VerificationMethod.OCR_TEXT_DISAPPEARED,
        ):
            verify_level = PerceptionLevel.LEVEL_3_OCR
        elif last_action and last_action.verification_method == VerificationMethod.SCREEN_DELTA:
            verify_level = PerceptionLevel.LEVEL_4_TARGETED_CV
        post_perception = self._observe_for_task(
            verify_level,
            force_refresh=True,
        )

        # Verify (the action's own product rides along for read steps)
        verification = await self.verification.verify(
            action=self._task_state.last_action,
            expected=self._task_state.expected_result,
            perception_before=self._task_state.last_observation,
            perception_after=post_perception,
            actual_result=self._task_state.actual_result,
        )

        # Window teardown (and similar animated transitions) can outlast a
        # single capture round-trip: the action already succeeded, only the
        # observation lags. Focus moves, titles update mid-navigation,
        # tab strips animate, render lags typing. Any STATE-observation
        # check (window, focus, OCR text, pixel diff) that fails gets ONE
        # delayed re-capture instead of failing. Disk/UIA-truth checks
        # (FILE_EXISTS, TEXT_READ, CLIPBOARD) are truthful first try and
        # stay single-shot. This never re-executes the action, so a
        # destructive step is never blindly repeated. Fast path unchanged
        # when the first capture proves it.
        _settleable = verification.method in (
            VerificationMethod.SCREEN_DELTA,
            VerificationMethod.WINDOW_APPEARED,
            VerificationMethod.FOCUSED_CONTROL,
            VerificationMethod.OCR_TEXT_APPEARED,
            VerificationMethod.OCR_TEXT_DISAPPEARED)
        if not verification.success and _settleable:
            await asyncio.sleep(
                2.5 if verification.method == VerificationMethod.SCREEN_DELTA
                else 1.5)
            post_perception = self.perception.observe(
                verify_level,
                force_refresh=True,
            )
            verification = await self.verification.verify(
                action=self._task_state.last_action,
                expected=self._task_state.expected_result,
                perception_before=self._task_state.last_observation,
                perception_after=post_perception,
            )
        
        # Phase 4B: stale-file guard. FILE_EXISTS can pass on a stale
        # file while a modal dialog is still open and undecided (an
        # overwrite confirmation, a folder-merge prompt, ...): the path
        # exists, but the operation did not complete. ANY open dialog at
        # this point turns the pass into an honest failure (recovery
        # path) instead of a false success. Observed in the wild: a
        # "Confirm Folder Replace" dialog left a file check green.
        if (verification.success
                and verification.method == VerificationMethod.FILE_EXISTS):
            try:
                dlg = classify_dialog(post_perception, {
                    "app": self._task_state.current_application,
                    "goal": self._task_state.goal,
                    "last_action_type": (
                        self._task_state.last_action.action_type
                        if self._task_state.last_action else ""),
                })
                if dlg.kind != DialogKind.NONE:
                    verification = VerificationResult(
                        success=False,
                        method=verification.method,
                        evidence="modal dialog still open; file state "
                                 "ambiguous (" + dlg.kind.value + ": "
                                 + dlg.evidence + ")",
                        confidence=0.0,
                        expected=verification.expected,
                        actual=verification.actual,
                    )
            except Exception:
                pass

        self._task_state.verification_result = verification
        self._publish_hand_action_feedback(
            self._task_state.last_action, "verified",
            bool(verification.success), verified=bool(verification.success))
        self._task_state.verification_method = verification.method
        self._set_side_effect_status(
            self._task_state.last_action,
            "VERIFIED" if verification.success else "UNKNOWN",
            getattr(verification, "evidence", "") if verification.success
            else "verification did not establish the side-effect outcome")
        # Adopt verification confidence only when it carries signal.
        # A failed check reports 0.0 by convention, but zeroing the task
        # here makes recovery's confidence gate escalate on the FIRST
        # failure — no retry ever happens. The retry budget (attempts +
        # decay in RecoveryEngine.decide) already bounds recovery, so a
        # zero must not masquerade as "no confidence left".
        if verification.confidence:
            self._task_state.confidence = verification.confidence

        if verification.success:
            # Mark step complete. A recovered step clears the stale
            # failure text: reporting an old failure next to a finished
            # task reads as a contradiction (seen live).
            self._task_state.failure_reason = None
            step_result = self._task_state.last_action_result
            if step_result:
                step_result.success = True
                step_result.verification = verification
                if step_result.subgoal:
                    step_result.subgoal.completed = True
            self._task_state.completed_steps.append(step_result)
            
            # Advance to next step or complete
            if self._task_state.advance_step():
                self._transition(OrchestratorState.OBSERVING)
            else:
                self._transition(OrchestratorState.DONE)
        else:
            # Verification failed - enter recovery. When a file check
            # fails on a Windows-illegal name, say so plainly: "not
            # found" alone never explains that the name itself was
            # impossible. Generic: applies to every file task.
            _reason = verification.evidence
            try:
                _fp = (getattr(
                    self._task_state.expected_result, "file_path", "")
                    or "")
                if _fp and verification.method == VerificationMethod.FILE_EXISTS:
                    import os as _os
                    _ok, _why = validate_filename(
                        _os.path.basename(_fp))
                    if not _ok:
                        _reason = (_reason + "; filename invalid on "
                                   f"Windows: {_why}")
            except Exception:
                pass
            self._task_state.failure_reason = _reason
            try:
                metrics = self._task_state.metrics
                metrics["verification_failures"] = metrics.get(
                    "verification_failures", 0) + 1
            except Exception:
                pass
            self._transition(OrchestratorState.RECOVERING)
            
            # Phase 2F: Detect adaptation need on verification failure
            if self.adaptation_manager and getattr(self._task_state, "current_plan", None):
                try:
                    # Get the procedure ID from the plan (if available)
                    plan = self._task_state.current_plan
                    if plan.subgoals:
                        # Try to find a matching procedure
                        proc_store = get_procedure_store()
                        procs = proc_store.find_by_goal(self._task_state.goal, min_confidence=0.5)
                        if procs:
                            # Get perception before the failed action
                            perception_before = self._task_state.last_observation
                            perception_after = self.perception.observe(force_refresh=True)
                            
                            adaptation = self.adaptation_manager.detect_adaptation_need(
                                base_procedure_id=procs[0].id,
                                perception_before=perception_before,
                                perception_after=perception_after,
                                failed_verification=self._task_state.verification_result,
                            )
                            if adaptation:
                                # Store the adaptation for future use
                                from .procedure_adaptation import get_adaptation_store
                                adaptation_store = get_adaptation_store()
                                adaptation_store.save(adaptation)
                                log.info(f"Created adaptation candidate for: {self._task_state.goal}")
                except Exception as e:
                    log.warning(f"Adaptation detection failed: {e}")
    
    async def _do_recovering(self) -> None:
        """Attempt to recover from failed verification."""
        if not self._task_state.can_retry():
            log.error("Max retries exceeded, failing task")
            self._transition(OrchestratorState.FAILED)
            return
        
        self._task_state.voice_state = VoiceState.RECOVERING
        try:
            metrics = self._task_state.metrics
            metrics["recoveries"] = metrics.get("recoveries", 0) + 1
        except Exception:
            pass

        # Fresh observation for recovery context
        perception = self._observe_for_task(
            PerceptionLevel.LEVEL_2_UIA_TREE, force_refresh=True)

        # Phase 4B: classify any foreground dialog so the recovery
        # decision (and later failure learning) can see what kind of
        # unexpected state appeared. Read-only enrichment only.
        dialog_posture = None
        try:
            dlg = classify_dialog(perception, {
                "app": self._task_state.current_application,
                "goal": self._task_state.goal,
                "last_action_type": (
                    self._task_state.last_action.action_type
                    if self._task_state.last_action else ""),
            })
            dialog_posture = dlg.posture
            self._task_state.recovery_history.append({
                "phase": "dialog_classification",
                "kind": dlg.kind.value,
                "posture": dlg.posture.value,
                "evidence": dlg.evidence,
                "timestamp": time.time(),
            })
        except Exception as e:
            log.warning(f"Dialog classification failed: {e}")

        side_effect = self.side_effect_contract(self._task_state.last_action)
        if ((self._action_fence_enabled or callable(self.handoff_notifier)) and
                self._task_state.last_action is not None and
                self._task_state.side_effect_status in {
                    "UNKNOWN", "EXECUTED_UNVERIFIED", "FAILED",
                    "HANDOFF_REQUIRED",
                } and side_effect["replay_policy"] != REPLAY_SAFE_RETRY
                and not (self._task_state.side_effect_status == "RETRY_ALLOWED"
                         and self._task_state.side_effect_retry_count > 0)):
            self.handoff_to_user(
                self._task_state.failure_reason or
                "side-effect outcome is ambiguous; replay is not safe",
                self._task_state.last_action)
            return

        # Phase 6: idempotent creation. A replace/merge confirmation
        # for a folder whose target ALREADY exists on disk proves the
        # goal state ("create X" with X present = satisfied, including
        # whole-task retries colliding with their own artifacts). Escape
        # chooses nothing destructive; the step then completes through
        # the normal FILE_EXISTS re-verification, never by assertion.
        # Scoped to folder-creation steps ONLY: a same-name file-save
        # confirm must never auto-complete (it would bless stale bytes).
        try:
            from .dialogs import DialogKind
            _last = self._task_state.last_action
            _exp = self._task_state.expected_result
            _fpath = (getattr(_exp, "file_path", "") or "")
            _desc = (((getattr(_last, "target_description", "") or "")
                       + " " + str(getattr(
                           self._task_state.current_subgoal(), "description",
                           "") or "")).lower() if _last else "")
            import os as _os
            if (_last is not None and _fpath and _os.path.exists(_fpath)
                    and dlg.kind in (DialogKind.CONFIRM_GENERIC,
                                     DialogKind.CONFIRM_OVERWRITE)
                    and (((_last.action_type or "").lower() in (
                        "create_folder", "create_directory", "make_folder"))
                         or "folder" in _desc)):
                from .state import TargetSpec as _TS
                _esc = Action(
                    task_id=self._task_state.task_id,
                    step_id=self._task_state.current_step,
                    intent="dismiss already-satisfied replace confirm",
                    action_type="hotkey",
                    target=_TS(text_match="escape"),
                    target_description="escape",
                    grounding_method=GroundingMethod.KEYBOARD_DIRECT,
                    expected_result=ExpectedResult(),
                    verification_method=VerificationMethod.CUSTOM,
                    risk_level=RiskLevel.LOW,
                )
                if not self._action_budget_available("idempotent recovery"):
                    return
                self._record_recovery_action()
                _esc_res = await self.action_executor.execute(
                    action=_esc,
                    perception=perception,
                    permission_state=None,
                )
                self._task_state.recovery_history.append({
                    "phase": "idempotent_create",
                    "target": _fpath,
                    "esc": _esc_res.success,
                    "timestamp": time.time(),
                })
                if _esc_res.success:
                    _fresh = self._observe_for_task(
                        PerceptionLevel.LEVEL_2_UIA_TREE,
                        force_refresh=True)
                    _reverify = await self.verification.verify(
                        action=_last,
                        expected=_exp,
                        perception_before=self._task_state.last_observation,
                        perception_after=_fresh,
                    )
                    if _reverify.success:
                        self._task_state.last_observation = _fresh
                        self._task_state.verification_result = _reverify
                        self._task_state.confidence = _reverify.confidence
                        _sr = self._task_state.last_action_result
                        if _sr:
                            _sr.success = True
                            _sr.verification = _reverify
                        self._task_state.completed_steps.append(_sr)
                        self._task_state.recovery_attempts += 1
                        if self._task_state.advance_step():
                            self._transition(OrchestratorState.OBSERVING)
                        else:
                            self._transition(OrchestratorState.DONE)
                        return
        except Exception as e:
            log.warning(f"Idempotent-create recovery failed: {e}")

        # A DISMISS-posture dialog (single-OK info/error box) decides
        # nothing, so dismissing it with Escape is always safe and lets
        # the task continue from a clean state. Anything else falls
        # through to the normal recovery decision below.
        try:
            from .dialogs import DialogPosture
            if dialog_posture == DialogPosture.DISMISS:
                from .state import TargetSpec
                esc_action = Action(
                    task_id=self._task_state.task_id,
                    step_id=self._task_state.current_step,
                    intent="dismiss blocking message dialog",
                    action_type="hotkey",
                    target=TargetSpec(text_match="escape"),
                    target_description="escape",
                    grounding_method=GroundingMethod.KEYBOARD_DIRECT,
                    expected_result=ExpectedResult(),
                    verification_method=VerificationMethod.CUSTOM,
                    risk_level=RiskLevel.LOW,
                )
                if not self._action_budget_available("dialog dismissal"):
                    return
                self._record_recovery_action()
                esc_result = await self.action_executor.execute(
                    action=esc_action,
                    perception=perception,
                    permission_state=None,
                )
                self._task_state.recovery_history.append({
                    "phase": "dialog_dismiss",
                    "success": esc_result.success,
                    "message": esc_result.message,
                    "timestamp": time.time(),
                })
                if esc_result.success:
                    self._task_state.recovery_attempts += 1
                    self._transition(OrchestratorState.OBSERVING)
                    return
        except Exception as e:
            log.warning(f"Dialog dismiss failed: {e}")

        # Get recovery decision
        decision = await self.recovery.decide(
            task_state=self._task_state,
            perception=perception,
            verification=self._task_state.verification_result,
        )
        
        self._task_state.mark_recovery(decision.reason, decision.action)
        
        # Phase 3: Execute recovery action if executor is available
        recovery_executed = False
        if self.recovery_action_executor:
            if not self._action_budget_available("recovery action"):
                return
            self._record_recovery_action()
            recovery_executed = await self.recovery_action_executor.execute_recovery(
                decision=decision,
                task_state=self._task_state,
                perception=perception,
                verification=self._task_state.verification_result,
            )
        
        if decision.should_retry or recovery_executed:
            # Re-plan with recovery context
            self._task_state.recovery_attempts += 1
            if side_effect["replay_policy"] == REPLAY_SAFE_RETRY:
                self._set_side_effect_status(
                    self._task_state.last_action, "RETRY_ALLOWED",
                    "existing bounded recovery permits a safe retry")
            # Could modify action or try different grounding
            self._transition(OrchestratorState.OBSERVING)
        else:
            # Phase 13: before failing, try an adapted verified procedure.
            # Same goal, rebound literals, re-grounded against the live UI,
            # executed through the normal act/verify loop afterwards.
            try:
                if await self._try_procedure_adaptation(perception):
                    return
            except Exception as e:
                log.warning(f"procedure adaptation attempt failed: {e}")
            self._transition(OrchestratorState.FAILED)
        
        # Phase 2E: Notify ProcedureLearner of verification failure for failure learning
        if self.procedure_learner:
            self.procedure_learner.on_verification_failed(
                task_state=self._task_state,
                perception=perception,
                verification_result=self._task_state.verification_result,
            )
    
    async def _try_procedure_adaptation(self, perception) -> bool:
        """Retry the failed goal with an adapted verified procedure.

        Looks up a stored procedure for the same goal, rebinds its
        literals to this run, re-checks targets against the live UI, and
        swaps the remaining plan so the normal act/verify loop executes
        it. Returns True when an adapted plan took over.
        """
        try:
            if not self.adaptation_manager or self._task_state is None:
                return False
            goal = self._task_state.goal or ""
            if not goal:
                return False
            from .procedure_adaptation import (
                get_adaptation_store, ProcedureAdaptation)
            from .procedure_store import get_procedure_store
            store = get_procedure_store()
            procs = store.find_by_goal(goal, 0.5) or []
            if not procs:
                try:
                    normed = store._normalize_goal(goal, [])
                    if normed and normed != goal:
                        procs = store.find_by_goal(normed, 0.5) or []
                except Exception:
                    pass
            if not procs:
                return False
            base = procs[0]
            adapted, record = self.adaptation_manager.adapt_plan(
                base, goal, perception)
            if not adapted:
                return False
            if not self.adaptation_manager.execute_adaptation(
                    record, self._task_state, adapted):
                return False
            try:
                adapt_obj = ProcedureAdaptation(
                    base_procedure_id=getattr(base, "id", None) or 0,
                    trigger=str(record.get("trigger", "recovery_replan")),
                    context_diff={},
                    parameter_changes=dict(
                        record.get("parameter_changes", {}) or {}),
                    step_modifications=list(
                        record.get("step_modifications", []) or []),
                    reason=str(record.get("reason", ""))[:300],
                    status="candidate",
                )
                aid = get_adaptation_store().save(adapt_obj)
                record["adaptation_id"] = aid
                self._task_state.adaptation_record = record
            except Exception as e:
                log.warning(f"adaptation record save failed: {e}")
            log.info("adaptation applied: base=%s trigger=%s steps=%d",
                     getattr(base, "id", "?"), record.get("trigger"),
                     len(adapted))
            self._transition(OrchestratorState.OBSERVING)
            return True
        except Exception as e:
            log.warning(f"_try_procedure_adaptation failed: {e}")
            return False

    async def _do_speaking(self) -> None:
        """Handle TTS output (voice integration)."""
        self._task_state.voice_state = VoiceState.SPEAKING
        # Voice output handled by voice system
        await asyncio.sleep(0)  # Yield
    
    async def _do_interrupted(self) -> None:
        """Handle barge-in interruption."""
        self._task_state.voice_state = VoiceState.INTERRUPTED
        
        # Classify interruption relation
        relation = self._classify_interrupt(self._task_state.user_interrupt)
        
        if relation == InterruptRelation.CANCELLATION:
            self._transition(OrchestratorState.FAILED)
        elif relation == InterruptRelation.MODIFICATION:
            # Modify current goal and re-plan
            self._task_state.goal = self._merge_goal(
                self._task_state.goal, 
                self._task_state.user_interrupt
            )
            self._transition(OrchestratorState.UNDERSTANDING)
        elif relation == InterruptRelation.NEW_TASK:
            # Queue new task, fail current
            self._transition(OrchestratorState.FAILED)
        else:
            # Clarification or unrelated - answer and resume
            self._transition(OrchestratorState.UNDERSTANDING)
    
    async def _do_terminal(self) -> None:
        """Handle terminal states (DONE/FAILED)."""
        if self._state == OrchestratorState.DONE:
            self._task_state.voice_state = VoiceState.DONE
            # Report completion
            if self.voice and self._task_state.goal \
                    and not self._task_state.cancel_requested \
                    and not getattr(self, "quiet_voice", False):
                self.voice.say(f"Done. {self._task_state.goal}")
            # Phase 2E: Notify ProcedureLearner of successful completion
            if self.procedure_learner:
                self.procedure_learner.on_task_completed(
                    task_state=self._task_state,
                    perception_before=self._task_state.last_observation,
                    perception_after=None,
                    verification_result=self._task_state.verification_result,
                    orchestrator_state=self._state,
                )
            # Phase 13: a verified replay is evidence FOR the stored
            # procedure it came from (success count + confidence).
            try:
                _lp = getattr(self._task_state, "learned_procedure", None)
                _lid = getattr(_lp, "id", None) if _lp is not None else None
                if _lid:
                    from .procedure_store import get_procedure_store
                    get_procedure_store().record_success(_lid)
            except Exception as e:
                log.warning(f"reuse evidence record failed: {e}")
            # Phase 13: fold the outcome back into any applied adaptation,
            # judged by the adapted steps' own verified results.
            try:
                _rec = getattr(self._task_state, "adaptation_record", None)
                _aid = (_rec or {}).get("adaptation_id") if isinstance(_rec, dict) else None
                if _aid and self.adaptation_manager:
                    _ok = self.adaptation_manager.verify_adaptation(
                        _rec, self._task_state)
                    from .procedure_adaptation import get_adaptation_store
                    get_adaptation_store().record_adaptation_outcome(_aid, _ok)
                    log.info("adaptation outcome recorded: id=%s success=%s",
                             _aid, _ok)
            except Exception as e:
                log.warning(f"adaptation outcome record failed: {e}")
        else:
            self._task_state.voice_state = VoiceState.FAILED
            if self.voice and not self._task_state.cancel_requested \
                    and not getattr(self, "quiet_voice", False):
                self.voice.say("Sorry, I couldn't complete that.")
            # Phase 13: a failed run still teaches the applied adaptation.
            try:
                _rec = getattr(self._task_state, "adaptation_record", None)
                _aid = (_rec or {}).get("adaptation_id") if isinstance(_rec, dict) else None
                if _aid:
                    from .procedure_adaptation import get_adaptation_store
                    get_adaptation_store().record_adaptation_outcome(_aid, False)
            except Exception:
                pass
        
        self._running = False
        self._transition(OrchestratorState.IDLE)
    
    # Helper methods
    
    def _required_perception_level(self):
        """Determine required perception level for current action."""
        if self._task_state is None:
            return PerceptionLevel.LEVEL_2_UIA_TREE
        action = self._task_state.last_action
        if not action:
            return PerceptionLevel.LEVEL_2_UIA_TREE
        
        # OCR-based verification needs OCR capture regardless of grounding method.
        if action.verification_method in (VerificationMethod.OCR_TEXT_APPEARED, VerificationMethod.OCR_TEXT_DISAPPEARED):
            return PerceptionLevel.LEVEL_3_OCR
        # Screen-delta verification needs before/after screenshots.
        if action.verification_method == VerificationMethod.SCREEN_DELTA:
            return PerceptionLevel.LEVEL_4_TARGETED_CV
        if action.grounding_method in (GroundingMethod.UIA, GroundingMethod.UIA_ROLE):
            return PerceptionLevel.LEVEL_2_UIA_TREE
        elif action.grounding_method == GroundingMethod.OCR_TEXT:
            return PerceptionLevel.LEVEL_3_OCR
        elif action.grounding_method in (GroundingMethod.CV_TEMPLATE, GroundingMethod.RELATIVE_COORDS):
            return PerceptionLevel.LEVEL_4_TARGETED_CV
        return PerceptionLevel.LEVEL_2_UIA_TREE
    
    def _retrieve_memory(self, query: str) -> "MemoryBundle":
        """Retrieve relevant memory for the query through the capability
        bus (memory/job/procedure systems), not an empty stub."""
        from .state import MemoryBundle
        if not self.memory:
            return MemoryBundle()
        bundle = MemoryBundle()
        try:
            bus = getattr(self, "cap_bus", None)
            if bus is None:
                from core.capability_bus import CapabilityBus
                bus = CapabilityBus()
                bus.attach(memory=self.memory)
            mem = bus.request("MEMORY_RECALL",
                              {"query": query, "limit": 4})
            if mem.get("success"):
                bundle.facts = mem.get("data") or []
            job = bus.request("JOB_MEMORY", {"query": query, "limit": 2})
            if job.get("success"):
                bundle.work_context = {"jobs": job.get("data") or []}
            proc = bus.request("PROCEDURES",
                               {"goal": query, "min_confidence": 0.5})
            if proc.get("success"):
                bundle.procedures = proc.get("data") or []
        except Exception:
            pass
        return bundle
    
    def _valid_model_action(self, atype: str) -> bool:
        """Validate a model-proposed action type against what the system
        can actually execute: registered skill names plus the engine's
        implemented action verbs. Capability truth, not phrasing."""
        a = (atype or "").strip().lower()
        if a in ("type_text", "type", "hotkey", "click", "ui_click",
                 "double_click", "read_text", "read_screen_text",
                 "verify_file", "screenshot", "open_url", "scroll",
                 "open_app", "open_application", "close_app",
                 "close_application", "create_folder", "create_directory",
                 "make_folder", "write_file", "create_file", "read_file",
                 "execute"):
            return True
        try:
            from .skills import get_skill_registry
            return any(getattr(s, "name", "") == a
                       for s in get_skill_registry()._skills.values())
        except Exception:
            return False

    _MODEL_ACTION_TYPES = ("open_app", "close_app", "click", "type_text",
                             "hotkey", "scroll", "screenshot", "read_text")

    async def _decompose_via_model(self, goal: str, part: str,
                                   max_steps: int = 10) -> list:
        """General planning for unknown work: the model proposes concrete
        capability steps for the remaining part; DUDE validates types,
        executes through existing executors, and verifies. Model output
        that is not a valid capability step is discarded, never acted on.
        Returns [] when the model cannot plan (caller falls back)."""
        import re as _re
        try:
            request = GenerateRequest(
                prompt=("You plan computer work. Overall user goal: "
                        f"{goal[:300]}\nAlready planned/executed: done.\n"
                        f"Plan ONLY this remaining part: {part[:300]}\n"
                        "Reply with numbered steps, one per line, EXACTLY "
                        "in format: ACTION_TYPE | target | expected outcome\n"
                        "Valid ACTION_TYPE: open_app, close_app, click, "
                        "type_text, hotkey, scroll, screenshot, read_text. "
                        "Targets: app names, 'Name | "
                        "Role' controls, literal text, hotkeys like ctrl+s. "
                        "No prose, max 10 lines."),
            )
            response = await self.intelligence.generate(request)
            steps = []
            for line in str(getattr(response, "content", "") or "").splitlines():
                m = _re.match(r"\s*\d+[\).\]]\s*([^|]+)\|([^|]+)\|?(.*)$",
                              line.strip())
                if not m:
                    continue
                atype = m.group(1).strip().lower()
                if not self._valid_model_action(atype):
                    continue
                steps.append(SubGoal(
                    description=m.group(2).strip()[:120] or part[:80],
                    intent=goal,
                    action_type=atype,
                    target_description=m.group(2).strip()[:200],
                    expected_result=m.group(3).strip()[:120],
                    verification_method=VerificationMethod.CUSTOM,
                    risk_level=RiskLevel.LOW,
                ))
                if len(steps) >= max_steps:
                    break
            return steps
        except Exception:
            return []

    async def _decompose_goal(self, goal: str) -> list[SubGoal]:
        """Decompose user goal into subgoals using intelligence."""
        if self.intelligence is not None:
            steps = await self._decompose_via_model(goal, goal)
            if steps:
                return steps
        return [
            SubGoal(
                description=f"Execute: {goal}",
                intent=goal,
                action_type="execute",
                target_description=goal,
                verification_method=VerificationMethod.CUSTOM,
            )
        ]
    
    async def _answer_directly(self, query: str) -> None:
        """Answer simple query directly via intelligence."""
        request = GenerateRequest(prompt=query)
        response = await self.intelligence.generate(request)
        if self.voice and not self._task_state.cancel_requested \
                and not getattr(self, "quiet_voice", False):
            self.voice.say(response.content)
    
    async def _plan_action(self, subgoal: SubGoal) -> Action:
        """Plan concrete action for subgoal."""
        # For Phase 1: create basic action from subgoal
        expected = ExpectedResult()
        _open_hwnd, _open_fresh = None, False
        if subgoal.expected_result:
            expected.text_expected = subgoal.expected_result
        elif subgoal.action_type in ("open_app", "open_application"):
            # Extract app name from target_description (e.g., "open notepad" -> "notepad")
            target = subgoal.target_description.lower()
            for prefix in ("open ", "launch ", "start "):
                if target.startswith(prefix):
                    target = target[len(prefix):]
                    break
            # Voice-transcript hygiene (matches router extraction):
            # trailing punctuation and stray spacing must not reach the
            # identity checks — "Notepad." and "note pad" are "notepad".
            import re as _re
            target = _re.sub(r'\s+', ' ', target).strip().rstrip('.,!?;:')
            expected.window_title = target
            expected.process_name = target
            subgoal.verification_method = VerificationMethod.WINDOW_APPEARED
            # Phase 5: reuse instead of relaunch. Resolve the app against
            # live windows and pin the outcome on the target: an exact
            # HWND to focus, or a fresh-window request when the task wants
            # isolation (or every candidate is unusable). Best effort and
            # strictly additive: failures fall back to the legacy target.
            _fresh, _hwnd = False, None
            try:
                from .app_instances import (
                    AppInstanceManager, ResolveResult, ReuseDecision)
                _manager = AppInstanceManager()
                if self._task_state.prefer_fresh_windows:
                    AppInstanceManager.record(
                        self._task_state, target, ResolveResult(
                            decision=ReuseDecision.OPEN_NEW,
                            reason="task prefers fresh windows"))
                    _fresh = True
                else:
                    _res = _manager.resolve(target, self._task_state)
                    AppInstanceManager.record(
                        self._task_state, target, _res)
                    _fresh = _res.decision in (
                        ReuseDecision.OPEN_NEW, ReuseDecision.UNSAFE_TO_REUSE)
                    if (_res.decision == ReuseDecision.REUSE_EXISTING
                            and _res.window is not None):
                        _hwnd = _res.window.hwnd
            except Exception:
                _fresh, _hwnd = False, None
            # Wire the resolve outcome onto the action: an exact HWND to
            # focus, or a fresh-window request. Without this the decision
            # above is recorded but never executed (the tool falls back
            # to name-based guessing).
            _open_hwnd, _open_fresh = _hwnd, _fresh
        elif subgoal.action_type in ("close_app", "close_application"):
            target = subgoal.target_description.lower()
            for prefix in ("close ", "exit ", "quit "):
                if target.startswith(prefix):
                    target = target[len(prefix):]
                    break
            import re as _re
            target = _re.sub(r'\s+', ' ', target).strip().rstrip('.,!?;:')
            expected.window_title = target
            expected.process_name = target
        elif subgoal.action_type in ("create_folder", "create_directory", "make_folder"):
            # Extract folder name from target_description (e.g., "create folder Reports" -> "Reports")
            target = subgoal.target_description
            target_lower = target.lower()
            for prefix in ("create folder ", "create directory ", "make folder ", "mkdir ", "create a folder ", "create a directory "):
                if target.lower().startswith(prefix):
                    target = target[len(prefix):]
                    break
            # Construct full path on Desktop
            import os
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            full_path = os.path.join(desktop_path, target)
            expected.file_path = full_path
            expected.window_title = target
            # For create_folder, the target text_match should be the full path for the tool
            # But we also need to preserve the original target_description for display
            full_path = os.path.join(os.path.expanduser("~"), "Desktop", target)
        elif subgoal.action_type in ("write_file", "create_file"):
            # Parse "path|content" format
            target = subgoal.target_description
            parts = target.split("|", 1)
            if len(parts) == 2:
                expected.file_path = parts[0].strip()
            else:
                expected.file_path = target
            expected.text_expected = parts[1].strip() if len(parts) == 2 else ""
        # Default target: text match against OCR/perception text.
        target_spec = TargetSpec(text_match=full_path if subgoal.action_type in ("create_folder", "create_directory", "make_folder") else subgoal.target_description)
        if subgoal.action_type in ("click", "click_control", "ui_click", "double_click", "read_text"):
            # Ground against the live UIA tree by control name. Format:
            # "Name" or "Name | Role" — the role disambiguates repeated names
            # (e.g. Save dialog "File name:" exists as both EditControl and
            # ComboBoxControl). Keystrokes that follow a grounded click go to
            # the focused control, which is why typing itself stays keyboard-direct.
            # Reads use the same grounding, then fetch ValuePattern live.
            name_parts = [p.strip() for p in subgoal.target_description.split("|", 1)]
            target_spec = TargetSpec(
                control_name=name_parts[0],
                control_role=name_parts[1] if len(name_parts) > 1 and name_parts[1] else None,
            )
            if subgoal.verification_method == VerificationMethod.WINDOW_APPEARED:
                expected.window_title = subgoal.expected_result
            elif subgoal.verification_method == VerificationMethod.FILE_EXISTS:
                expected.file_path = subgoal.expected_result
        elif subgoal.action_type in ("hotkey",):
            # A hotkey that summons a dialog (e.g. Ctrl+Shift+S -> "Save as")
            # names the window it must produce in expected_result. A hotkey
            # verified by clipboard names the text the clipboard must hold
            # (e.g. a cut file's path); verified by file existence it names
            # the file that must exist afterwards.
            if subgoal.verification_method == VerificationMethod.WINDOW_APPEARED:
                expected.window_title = subgoal.expected_result
            elif subgoal.verification_method == VerificationMethod.CLIPBOARD_CONTENT:
                expected.text_expected = subgoal.expected_result
            elif subgoal.verification_method == VerificationMethod.FILE_EXISTS:
                expected.file_path = subgoal.expected_result
        if subgoal.action_type in ("type", "type_text"):
            # A typing step verified by window presence only asserts the
            # context still holds; the load-bearing check always follows
            # at the next transition (navigation arrival, file creation).
            if (subgoal.verification_method == VerificationMethod.WINDOW_APPEARED
                    and subgoal.expected_result):
                expected.window_title = subgoal.expected_result
        if subgoal.action_type in ("open_app", "open_application"):
            # Carry the reuse decision to the executor: an exact HWND to
            # focus, or a fresh-window request. Plain launches (no window
            # found) need neither flag.
            target_spec = TargetSpec(
                text_match=subgoal.target_description,
                hwnd=_open_hwnd, fresh_window=_open_fresh)
        if subgoal.action_type in ("read_text",) and (
                subgoal.verification_method == VerificationMethod.CUSTOM):
            # Reads are verified by the read producing text, never by
            # pixels: coerce the pairing whichever planning path forgot it.
            subgoal.verification_method = VerificationMethod.TEXT_READ
        # A step that can never verify (CUSTOM) is a certain failure;
        # when the action has one canonical check, use it. This only
        # upgrades certain-failure to honest verification (a wrong
        # window still fails the check and recovers safely).
        _custom_defaults = {
            "open_app": VerificationMethod.WINDOW_APPEARED,
            "open_application": VerificationMethod.WINDOW_APPEARED,
            "close_app": VerificationMethod.WINDOW_DISAPPEARED,
            "close_application": VerificationMethod.WINDOW_DISAPPEARED,
            "create_folder": VerificationMethod.FILE_EXISTS,
            "create_directory": VerificationMethod.FILE_EXISTS,
            "make_folder": VerificationMethod.FILE_EXISTS,
        }
        if (subgoal.verification_method == VerificationMethod.CUSTOM
                and (subgoal.action_type or "").lower()
                in _custom_defaults):
            subgoal.verification_method = _custom_defaults[
                (subgoal.action_type or "").lower()]
        if subgoal.action_type in ("verify_file",):
            # Verification-only step: nothing to actuate. The check
            # itself runs in the verification phase against this path.
            expected.file_path = subgoal.expected_result or (
                subgoal.target_description or "")
        if subgoal.action_type in ("hotkey", "type", "type_text"):
            # Anchor keyboard steps to the task's application: the nearest
            # preceding open_app step names it. Comparing live focus against
            # the CURRENT foreground instead would pass vacuously whenever
            # both drift together (e.g. the user Alt+Tabs mid-task), and
            # keystrokes would land in the wrong window.
            app = self._app_from_prior_open()
            if app:
                expected.process_name = app
        return Action(
            task_id=self._task_state.task_id,
            step_id=self._task_state.current_step,
            intent=subgoal.intent,
            action_type=subgoal.action_type or "execute",
            target=target_spec,
            target_description=subgoal.target_description,
            grounding_method=GroundingMethod.UIA,
            expected_result=expected,
            verification_method=subgoal.verification_method,
            risk_level=subgoal.risk_level,
        )
    
    def _app_from_prior_open(self) -> str:
        """Name of the app from the nearest preceding open_app subgoal."""
        if not self._task_state or not self._task_state.subgoals:
            return ""
        for sg in reversed(
                self._task_state.subgoals[:self._task_state.current_step]):
            if (sg.action_type or "").lower() in ("open_app",
                                                  "open_application"):
                target = (sg.target_description or "").lower()
                for prefix in ("open ", "launch ", "start ", "run "):
                    if target.startswith(prefix):
                        target = target[len(prefix):]
                        break
                return target.strip()
        return ""

    def _check_permission(self, action: Action) -> PermissionState:
        """Check if action requires permission."""
        if not self.permission_gate:
            return PermissionState.GRANTED

        # A deliberate hand gesture IS the user's confirmation. Plain pointer
        # actions on the desktop used to return PENDING, which blocked on a
        # spoken "yes" - impossible to answer while gesturing, so every hand
        # click timed out and the task was reported as "paused before side
        # effects" even though the user was actively pointing at a button.
        # These are low-risk, reversible pointer actions at a resolved target,
        # so the gesture grants them. Anything destructive or window-level
        # still goes through the gate below.
        params = getattr(action, "parameters", {}) or {}
        if bool(params.get("hand_semantic", False)):
            if str(getattr(action, "action_type", "")).lower() in {
                    "click_at", "click", "ui_click", "drag", "scroll"}:
                return PermissionState.GRANTED

        # Use existing PermissionGate API
        requires = self.permission_gate.requires_permission(action.action_type)
        if not requires:
            return PermissionState.NOT_REQUIRED
        
        # Would request confirmation here
        return PermissionState.PENDING
    
    def _classify_interrupt(self, utterance: str) -> InterruptRelation:
        """Classify how interruption relates to current task."""
        low = utterance.lower()
        if any(w in low for w in ("stop", "cancel", "abort", "never mind")):
            return InterruptRelation.CANCELLATION
        elif any(w in low for w in ("instead", "actually", "no ", "not ")):
            return InterruptRelation.MODIFICATION
        elif "?" in utterance or any(w in low for w in ("what", "how", "why")):
            return InterruptRelation.CLARIFICATION
        return InterruptRelation.UNRELATED
    
    def _merge_goal(self, current: str, interrupt: str) -> str:
        """Merge interruption into current goal."""
        return f"{current} (modified: {interrupt})"


def summarize_metrics(task_state) -> dict:
    """Summarize a task's efficiency from its own recorded state.

    Pure function of TaskState fields (metrics counters, window
    decisions, recovery history, step counts): no engine needed, which
    is what makes it unit-testable. Optimization here means fewer wrong
    actions, never skipped verification.
    """
    metrics = dict(getattr(task_state, "metrics", None) or {})
    decisions = list(getattr(task_state, "window_decisions", None) or [])
    history = list(getattr(task_state, "recovery_history", None) or [])
    launches = sum(1 for d in decisions
                   if d.get("decision") in ("open_new", "not_found"))
    reuses = sum(1 for d in decisions
                 if d.get("decision") == "reuse_existing")
    summary = {
        "total_duration_seconds": sum(
            v for k, v in metrics.items()
            if k.startswith("seconds_in_")),
        "planning_seconds": metrics.get("seconds_in_understanding", 0.0)
        + metrics.get("seconds_in_planning", 0.0),
        "perception_seconds": metrics.get("seconds_in_observing", 0.0),
        "steps": metrics.get("steps", 0),
        "actions": metrics.get("actions", 0),
        "action_failures": metrics.get("action_failures", 0),
        "retries": metrics.get("recoveries", 0),
        "recovery_count": metrics.get("recoveries", 0),
        "window_launches": launches,
        "window_reuses": reuses,
        "verification_failures": metrics.get("verification_failures", 0),
        "dialog_events": sum(
            1 for e in history
            if e.get("phase") == "dialog_classification"),
        "final_success": bool(
            getattr(task_state, "current_step", 0) >= len(
                getattr(task_state, "subgoals", None) or [])
            and len(getattr(task_state, "subgoals", None) or []) > 0),
    }
    return summary
