"""Cognitive Front Door - Single entry point for all user utterances.

Receives: transcript + live environment + self-model + active goal + memory
Produces: structured cognitive interpretation
Routes to: appropriate capability based on SEMANTIC understanding
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, List, Dict, Callable
from datetime import datetime

from .state import (
    TaskState,
    PerceptionSnapshot,
    MemoryBundle,
    VerificationMethod,
    GroundingMethod,
    RiskLevel,
)
from .intelligence_router import IntelligenceRouter, RouteDecision
from .skills import SkillRegistry, get_skill_registry
from .procedure_store import ProcedureStore, get_procedure_store
from core.capability_bus import CapabilityBus
from core.agent_state import AgentState

log = logging.getLogger(__name__)


class CognitiveMode(Enum):
    """Semantic response modes - NOT keyword rules."""
    CONVERSE = "converse"                    # Social/conversational
    ANSWER_FROM_KNOWLEDGE = "answer_knowledge"  # Factual recall
    ANSWER_FROM_LIVE_ENV = "answer_live_env"    # "what am I looking at?"
    COMPUTER_WORK = "computer_work"           # Requires action execution
    MEMORY_OPERATION = "memory_operation"     # "remember this", "what did I do?"
    NEEDS_CLARIFICATION = "needs_clarification" # Ambiguous intent


@dataclass
class CognitiveInterpretation:
    """Structured output of the cognitive front door."""
    intent_kind: CognitiveMode
    user_goal: str
    desired_outcome: str
    requires_computer_action: bool
    required_capabilities: List[str] = field(default_factory=list)
    response_needed: bool = True
    confidence: float = 0.0
    # For computer work: the structured plan/intention
    execution_plan: Optional[Any] = None
    # For live environment questions: what to perceive
    perception_focus: Optional[str] = None
    # Context for continuation
    active_task_id: Optional[str] = None
    is_continuation: bool = False
    correction_to: Optional[str] = None


class CognitiveFrontDoor:
    """
    Single cognitive entry point for ALL user utterances.
    
    Does NOT use regex/keywords to decide routes.
    Uses: fast LLM classification + live perception + self-model + memory
    """
    

    def _enrich_interpretation(
        self, 
        interpretation: CognitiveInterpretation, 
        context: Dict[str, Any]
    ) -> CognitiveInterpretation:
        """Add execution plan or perception focus based on mode."""

        if interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:
            # Get execution plan from IntelligenceRouter
            if self.intelligence_router and context["active_goal"]:
                try:
                    routing = self.intelligence_router.route(
                        task_state=context["active_goal"],
                        perception=context["perception"],
                        user_intent=context["transcript"],
                    )
                    interpretation.execution_plan = routing.plan
                    interpretation.intent_kind = CognitiveMode.COMPUTER_WORK
                except Exception as e:
                    log.warning(f"Planning failed: {e}")

        elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
            interpretation.perception_focus = "full_screen_with_ocr"

        elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
            interpretation.required_capabilities = ["episodic_memory", "fact_memory"]

        return interpretation

    def __init__(
        self,
        capability_bus: CapabilityBus,
        agent_state: AgentState,
        intelligence_router: Optional[IntelligenceRouter] = None,
        memory: Optional[Any] = None,
        fast_classifier: Optional[Callable[[str, Dict], CognitiveInterpretation]] = None,
    ):
        self.capability_bus = capability_bus
        self.agent_state = agent_state
        self.intelligence_router = intelligence_router
        self.memory = memory
        self.fast_classifier = fast_classifier  # Fast path for simple classification
        
        # Self-model: what DUDE can actually do RIGHT NOW
        self._self_model = self._build_self_model()
        

    def _enrich_interpretation(
        self, 
        interpretation: CognitiveInterpretation, 
        context: Dict[str, Any]
    ) -> CognitiveInterpretation:
        """Add execution plan or perception focus based on mode."""

        if interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:
            # Get execution plan from IntelligenceRouter
            if self.intelligence_router and context["active_goal"]:
                try:
                    routing = self.intelligence_router.route(
                        task_state=context["active_goal"],
                        perception=context["perception"],
                        user_intent=context["transcript"],
                    )
                    interpretation.execution_plan = routing.plan
                    interpretation.intent_kind = CognitiveMode.COMPUTER_WORK
                except Exception as e:
                    log.warning(f"Planning failed: {e}")

        elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
            interpretation.perception_focus = "full_screen_with_ocr"

        elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
            interpretation.required_capabilities = ["episodic_memory", "fact_memory"]

        return interpretation

    def _build_self_model(self) -> Dict[str, Any]:
        """Build runtime self-model from actual component state with health status."""
        caps_available = []
        caps_degraded = []
        caps_unavailable = []
        
        def check_cap(name: str, check_fn) -> None:
            try:
                if check_fn():
                    caps_available.append(name)
                else:
                    caps_degraded.append(name)
            except Exception:
                caps_unavailable.append(name)
        
        # Perception
        check_cap("live_ui_observation", lambda: self.capability_bus.perception is not None)
        check_cap("screen_text_ocr", lambda: self.capability_bus.perception is not None)
        check_cap("window_enumeration", lambda: self.capability_bus.perception is not None)
        
        # Action execution
        check_cap("ui_click", lambda: self.capability_bus.action_executor is not None)
        check_cap("type_text", lambda: self.capability_bus.action_executor is not None)
        check_cap("hotkey", lambda: self.capability_bus.action_executor is not None)
        check_cap("open_app", lambda: self.capability_bus.action_executor is not None)
        check_cap("close_app", lambda: self.capability_bus.action_executor is not None)
        check_cap("window_operations", lambda: self.capability_bus.action_executor is not None)
        
        # Verification
        check_cap("outcome_verification", lambda: self.capability_bus.verification is not None)
        
        # Recovery
        check_cap("automatic_recovery", lambda: self.capability_bus.recovery is not None)
        
        # Memory
        check_cap("episodic_memory", lambda: self.capability_bus.memory is not None)
        check_cap("procedural_memory", lambda: self.capability_bus.memory is not None)
        check_cap("fact_memory", lambda: self.capability_bus.memory is not None)
        
        # Reasoning
        check_cap("local_planning", lambda: self.intelligence_router is not None)
        check_cap("skill_matching", lambda: self.intelligence_router is not None)
        check_cap("procedure_matching", lambda: self.intelligence_router is not None)
        check_cap("model_fallback", lambda: self.intelligence_router is not None)
        
        # Voice (always available if wired)
        check_cap("text_to_speech", lambda: True)
        check_cap("speech_to_text", lambda: True)
        
        return {
            "identity": "DUDE - autonomous computer coworker",
            "capabilities_available": caps_available,
            "capabilities_degraded": caps_degraded,
            "capabilities_unavailable": caps_unavailable,
            "capability_bus": "operational",
        }
    
    def process_turn(
        self,
        transcript: str,
        perception: Optional[PerceptionSnapshot] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> CognitiveInterpretation:
        """
        Main entry point: one utterance → one cognitive interpretation.
        
        Args:
            transcript: Raw STT output
            perception: Live perception snapshot (will fetch if not provided)
            context: Additional context (interrupt_brief, etc.)
        """
        if not transcript or not transcript.strip():
            return CognitiveInterpretation(
                intent_kind=CognitiveMode.CONVERSE,
                user_goal="",
                desired_outcome="acknowledge presence",
                requires_computer_action=False,
                confidence=0.1,
            )
        
        # 1. GET LIVE ENVIRONMENT (if not provided)
        if perception is None:
            perception = self._get_fresh_perception()
        
        # 2. GET ACTIVE GOAL/TASK CONTEXT
        active_goal = self._get_active_goal_context()
        
        # 3. GET RELEVANT MEMORY
        relevant_memory = self._get_relevant_memory(transcript)
        
        # 4. BUILD COGNITIVE CONTEXT
        cognitive_context = {
            "transcript": transcript,
            "perception": perception,
            "active_goal": active_goal,
            "memory": relevant_memory,
            "self_model": self._self_model,
            "timestamp": datetime.now().isoformat(),
        }
        
        # 5. FAST CLASSIFICATION (semantic, not keyword)
        interpretation = self._classify_intent(cognitive_context)
        
        # 6. ENRICH BASED ON MODE
        interpretation = self._enrich_interpretation(interpretation, cognitive_context)
        
        log.info(
            "COGNITIVE_FRONT_DOOR: mode=%s goal=%s action=%s conf=%.2f",
            interpretation.intent_kind.value,
            interpretation.user_goal[:60],
            interpretation.requires_computer_action,
            interpretation.confidence,
        )
        
        return interpretation
    

    def _enrich_interpretation(
        self, 
        interpretation: CognitiveInterpretation, 
        context: Dict[str, Any]
    ) -> CognitiveInterpretation:
        """Add execution plan or perception focus based on mode."""

        if interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:
            # Get execution plan from IntelligenceRouter
            if self.intelligence_router and context["active_goal"]:
                try:
                    routing = self.intelligence_router.route(
                        task_state=context["active_goal"],
                        perception=context["perception"],
                        user_intent=context["transcript"],
                    )
                    interpretation.execution_plan = routing.plan
                    interpretation.intent_kind = CognitiveMode.COMPUTER_WORK
                except Exception as e:
                    log.warning(f"Planning failed: {e}")

        elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
            interpretation.perception_focus = "full_screen_with_ocr"

        elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
            interpretation.required_capabilities = ["episodic_memory", "fact_memory"]

        return interpretation

    def _get_fresh_perception(self) -> PerceptionSnapshot:
        """Fetch fresh perception through CapabilityBus."""
        try:
            from .state import PerceptionLevel
            return self.capability_bus.perception.observe(
                required_level=PerceptionLevel.LEVEL_2_UIA_TREE,
                force_refresh=True
            )
        except Exception as e:
            log.warning(f"Perception fetch failed: {e}")
            return PerceptionSnapshot()
    

    def _enrich_interpretation(
        self, 
        interpretation: CognitiveInterpretation, 
        context: Dict[str, Any]
    ) -> CognitiveInterpretation:
        """Add execution plan or perception focus based on mode."""

        if interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:
            # Get execution plan from IntelligenceRouter
            if self.intelligence_router and context["active_goal"]:
                try:
                    routing = self.intelligence_router.route(
                        task_state=context["active_goal"],
                        perception=context["perception"],
                        user_intent=context["transcript"],
                    )
                    interpretation.execution_plan = routing.plan
                    interpretation.intent_kind = CognitiveMode.COMPUTER_WORK
                except Exception as e:
                    log.warning(f"Planning failed: {e}")

        elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
            interpretation.perception_focus = "full_screen_with_ocr"

        elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
            interpretation.required_capabilities = ["episodic_memory", "fact_memory"]

        return interpretation

    def _get_active_goal_context(self) -> Optional[Dict[str, Any]]:
        """Get current active task/goal from AgentState using public API."""
        try:
            if self.agent_state:
                snap = self.agent_state.snapshot()
                goal = snap.get("goal", "")
                if goal:
                    return {
                        "task_id": f"turn_{snap.get('turn_id', 0)}",
                        "goal": snap.get("goal", ""),
                        "task": snap.get("task", ""),
                        "step": snap.get("step", ""),
                        "step_index": snap.get("step_index", 0),
                        "progress": snap.get("progress", ""),
                        "app": snap.get("app", ""),
                        "window": snap.get("window", ""),
                        "focus": snap.get("focus", ""),
                        "correction": snap.get("correction", ""),
                        "verified": snap.get("verified"),
                        "retries": snap.get("retries", 0),
                    }
        except Exception:
            pass
        return None
    

    def _enrich_interpretation(
        self, 
        interpretation: CognitiveInterpretation, 
        context: Dict[str, Any]
    ) -> CognitiveInterpretation:
        """Add execution plan or perception focus based on mode."""

        if interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:
            # Get execution plan from IntelligenceRouter
            if self.intelligence_router and context["active_goal"]:
                try:
                    routing = self.intelligence_router.route(
                        task_state=context["active_goal"],
                        perception=context["perception"],
                        user_intent=context["transcript"],
                    )
                    interpretation.execution_plan = routing.plan
                    interpretation.intent_kind = CognitiveMode.COMPUTER_WORK
                except Exception as e:
                    log.warning(f"Planning failed: {e}")

        elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
            interpretation.perception_focus = "full_screen_with_ocr"

        elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
            interpretation.required_capabilities = ["episodic_memory", "fact_memory"]

        return interpretation

    def _get_relevant_memory(self, transcript: str) -> MemoryBundle:
        """Retrieve relevant memory through CapabilityBus."""
        try:
            # Use CapabilityBus for memory access
            result = self.capability_bus.request("MEMORY_RECALL", {
                "query": transcript,
                "limit": 5
            })
            if result.get("success") and result.get("data"):
                # Convert to MemoryBundle
                facts = result["data"]
                return MemoryBundle(
                    facts=[f.get("content", str(f)) for f in facts] if facts else [],
                    procedures=[],
                    experience_matches=[],
                    recent_messages=[],
                    work_context=None
                )
        except Exception as e:
            log.warning(f"Memory recall via CapabilityBus failed: {e}")
        return MemoryBundle()
    

    def _enrich_interpretation(
        self, 
        interpretation: CognitiveInterpretation, 
        context: Dict[str, Any]
    ) -> CognitiveInterpretation:
        """Add execution plan or perception focus based on mode."""

        if interpretation.intent_kind == CognitiveMode.COMPUTER_WORK:
            # Get execution plan from IntelligenceRouter
            if self.intelligence_router and context["active_goal"]:
                try:
                    routing = self.intelligence_router.route(
                        task_state=context["active_goal"],
                        perception=context["perception"],
                        user_intent=context["transcript"],
                    )
                    interpretation.execution_plan = routing.plan
                    interpretation.intent_kind = CognitiveMode.COMPUTER_WORK
                except Exception as e:
                    log.warning(f"Planning failed: {e}")

        elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
            interpretation.perception_focus = "full_screen_with_ocr"

        elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
            interpretation.required_capabilities = ["episodic_memory", "fact_memory"]

        return interpretation

    def _classify_intent(self, context: Dict[str, Any]) -> CognitiveInterpretation:
        """
        Classify intent SEMANTICALLY using the IntelligenceRouter.
        
        The IntelligenceRouter already handles the full fallback chain:
        deterministic skill -> verified procedure -> local reasoning -> model fallback -> NO_SOLUTION.
        We just need to call route() and translate the result.
        """
        if not self.intelligence_router:
            log.warning("No intelligence_router available for classification")
            return CognitiveInterpretation(
                intent_kind=CognitiveMode.NEEDS_CLARIFICATION,
                user_goal=context["transcript"],
                desired_outcome="clarify user intent (no intelligence router)",
                requires_computer_action=False,
                required_capabilities=[],
                confidence=0.2,
            )
        
        try:
            # Get fresh perception if not provided
            perception = context.get("perception")
            if perception is None:
                perception = self._get_fresh_perception()
            
            # Get active goal context
            active_goal = context.get("active_goal")
            if not active_goal:
                active_goal = self._get_active_goal_context()
            
            # Route through IntelligenceRouter - this handles ALL fallback logic
            routing_result = self.intelligence_router.route(
                task_state=active_goal or TaskState(goal=context["transcript"]),
                perception=context.get("perception") or self._get_fresh_perception(),
                user_intent=context["transcript"],
            )
            
            # Translate RoutingResult to CognitiveInterpretation
            return self._translate_routing_result(routing_result, context)
            
        except Exception as e:
            log.exception(f"IntelligenceRouter classification failed: {e}")
            # Last resort: return COMPUTER_WORK with low confidence
            # This lets the planner attempt to handle it rather than blocking
            return CognitiveInterpretation(
                intent_kind=CognitiveMode.COMPUTER_WORK,
                user_goal=context["transcript"],
                desired_outcome="execute user's request on computer",
                requires_computer_action=True,
                required_capabilities=["ui_click", "type_text", "open_app"],
                confidence=0.4,
            )

    def _translate_routing_result(self, routing_result, context: Dict[str, Any]) -> CognitiveInterpretation:
        """Translate IntelligenceRouter RoutingResult to CognitiveInterpretation."""
        decision = routing_result.decision
            
        # Map routing decisions to cognitive modes
        if decision == RouteDecision.DETERMINISTIC_SKILL:
                        return CognitiveInterpretation(
            intent_kind=CognitiveMode.COMPUTER_WORK,
            user_goal=context["transcript"],
            desired_outcome="execute deterministic skill",
            requires_computer_action=True,
            required_capabilities=["ui_click", "type_text", "open_app"],
            confidence=routing_result.confidence,
            execution_plan=routing_result.plan,
        )
            
        elif decision == RouteDecision.VERIFIED_PROCEDURE:
                        return CognitiveInterpretation(
            intent_kind=CognitiveMode.COMPUTER_WORK,
            user_goal=context["transcript"],
            desired_outcome="execute verified procedure",
            requires_computer_action=True,
            required_capabilities=["ui_click", "type_text", "open_app"],
            confidence=routing_result.confidence,
            execution_plan=routing_result.plan,
        )
            
        elif decision == RouteDecision.LOCAL_REASONING:
                        return CognitiveInterpretation(
            intent_kind=CognitiveMode.COMPUTER_WORK,
            user_goal=context["transcript"],
            desired_outcome="execute planned computer work",
            requires_computer_action=True,
            required_capabilities=["ui_click", "type_text", "open_app"],
            confidence=routing_result.confidence,
            execution_plan=routing_result.plan,
        )
            
        elif decision == RouteDecision.MODEL_FALLBACK:
                        return CognitiveInterpretation(
            intent_kind=CognitiveMode.COMPUTER_WORK,
            user_goal=context["transcript"],
            desired_outcome="execute planned computer work",
            requires_computer_action=True,
            required_capabilities=["ui_click", "type_text", "open_app"],
            confidence=routing_result.confidence,
            execution_plan=routing_result.plan,
        )
            
        elif decision == RouteDecision.NO_SOLUTION:
                        return CognitiveInterpretation(
            intent_kind=CognitiveMode.NEEDS_CLARIFICATION,
            user_goal=context["transcript"],
            desired_outcome="clarify user intent",
            requires_computer_action=False,
            required_capabilities=[],
            confidence=0.2,
        )
            
        # Default fallback
        return CognitiveInterpretation(
        intent_kind=CognitiveMode.COMPUTER_WORK,
        user_goal=context["transcript"],
        desired_outcome="execute user's request on computer",
        requires_computer_action=True,
        required_capabilities=["ui_click", "type_text", "open_app"],
        confidence=0.4,
        )
    


def create_cognitive_front_door(
    capability_bus: CapabilityBus,
    agent_state: AgentState,
    intelligence_router: Optional[IntelligenceRouter] = None,
    memory: Optional[Any] = None,
) -> CognitiveFrontDoor:
    """Factory for CognitiveFrontDoor with production dependencies."""
    return CognitiveFrontDoor(
        capability_bus=capability_bus,
        agent_state=agent_state,
        intelligence_router=intelligence_router,
        memory=memory,
    )