"""TaskEngine - Authoritative task runtime state machine.

Orchestrates the complete task lifecycle using structured state transitions.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

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
from .intelligence import IntelligenceBackend
from .intelligence_router import IntelligenceRouter, RouteDecision, RoutingResult
from .state import Plan
from .skills import get_skill_registry
from .procedure_store import get_procedure_store
from .procedure_learner import get_procedure_learner
from .procedure_adaptation import get_adaptation_manager


log = logging.getLogger(__name__)


class TransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


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
    ):
        self.intelligence = intelligence
        self.perception = perception
        self.action_executor = action_executor
        self.verification = verification
        self.recovery = recovery
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
        
        self._state = OrchestratorState.IDLE
        self._task_state: Optional[TaskState] = None
        self._running = False
        self._paused = False
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

        self._task_state = TaskState(goal=goal, goal_type=goal_type)
        self._task_state.voice_state = VoiceState.LISTENING
        self._task_state.prefer_fresh_windows = prefer_fresh_windows
        self._task_state.execution_mode = execution_mode
        self._running = True
        self._paused = False
        self._transition(OrchestratorState.LISTENING)
        return self._task_state
    
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

    def request_cancel(self) -> None:
        """Ask the running task to stop ("stop").

        Takes effect at the next step boundary: no new actions start,
        applications are left intact, and the cancellation is recorded.
        """
        if self._task_state:
            self._task_state.cancel_requested = True

    def pause(self) -> None:
        """Pause after the current step, preserving all task state."""
        self._paused = True

    async def resume(self) -> TaskState:
        """Continue a paused task (or run a fresh one) from its current
        state. Never restarts completed work: steps advance monotonically
        through current_step."""
        self._paused = False
        while (self._running and not self._paused
               and self._state not in (OrchestratorState.DONE,
                                       OrchestratorState.FAILED)):
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
            await self._step()

        # Handle terminal state cleanup (transitions to IDLE)
        if self._state in (OrchestratorState.DONE, OrchestratorState.FAILED):
            await self._do_terminal()

        return self._task_state
    
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
                return self.perception.observe_window(
                    self._task_state.target_hwnd, required_level)
        except Exception as e:
            log.warning(f"Pinned-window observe failed: {e}")
        return self.perception.observe(required_level,
                                       force_refresh=force_refresh)

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
                # Verified procedure - convert to subgoals
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
        
        self._transition(OrchestratorState.PLANNING)
    
    async def _do_planning(self) -> None:
        """Select next action for current subgoal."""
        subgoal = self._task_state.current_subgoal()
        if not subgoal:
            self._transition(OrchestratorState.DONE)
            return
        
        # Build action for current subgoal
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
                # Would request confirmation here
                pass
            else:
                self._task_state.permission_state = PermissionState.GRANTED
        
        self._transition(OrchestratorState.ACTING)
    
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
            self._transition(OrchestratorState.OBSERVING)
            return
        
        # Execute action
        result = await self.action_executor.execute(
            action=action,
            perception=self._task_state.last_observation,
            permission_state=self._task_state.permission_state,
        )
        
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
        self._task_state.expected_result = action.expected_result
        self._task_state.actual_result = result.actual_result
        try:
            metrics = self._task_state.metrics
            metrics["actions"] = metrics.get("actions", 0) + 1
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
        self._task_state.verification_method = verification.method
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
            if self.adaptation_manager and self._task_state.current_plan:
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
            recovery_executed = await self.recovery_action_executor.execute_recovery(
                decision=decision,
                task_state=self._task_state,
                perception=perception,
                verification=self._task_state.verification_result,
            )
        
        if decision.should_retry or recovery_executed:
            # Re-plan with recovery context
            self._task_state.recovery_attempts += 1
            # Could modify action or try different grounding
            self._transition(OrchestratorState.OBSERVING)
        else:
            self._transition(OrchestratorState.FAILED)
        
        # Phase 2E: Notify ProcedureLearner of verification failure for failure learning
        if self.procedure_learner:
            self.procedure_learner.on_verification_failed(
                task_state=self._task_state,
                perception=perception,
                verification_result=self._task_state.verification_result,
            )
    
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
        else:
            self._task_state.voice_state = VoiceState.FAILED
            if self.voice and not self._task_state.cancel_requested \
                    and not getattr(self, "quiet_voice", False):
                self.voice.say("Sorry, I couldn't complete that.")
        
        self._running = False
        self._transition(OrchestratorState.IDLE)
    
    # Helper methods
    
    def _required_perception_level(self):
        """Determine required perception level for current action."""
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
                 "double_click", "read_text", "verify_file", "screenshot",
                 "scroll", "open_app", "open_application", "close_app",
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