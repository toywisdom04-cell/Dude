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
    # Phase 15: optional located on-screen referent (SpatialTarget).
    # Set structurally (plan step / live-env focus), never by matching
    # the user's wording. None means "answer verbally, show nothing".
    spatial_reference: Optional[Any] = None
    # Phase 16: optional hand observation (pointed element dict).
    # Sensor context only — never an executable action.
    hand_context: Optional[Any] = None
    # Phase 17: bounded context bundle assembled before reasoning
    # (skills/procedures/jobs/memory/visual/capabilities). Advisory.
    context_bundle: Optional[Any] = None


class CognitiveFrontDoor:
    """
    Single cognitive entry point for ALL user utterances.
    
    Uses plan semantics + live perception + self-model + memory.
    A conservative request-shape guard distinguishes questions from orders
    when the task router has no actionable plan (or matched question content).
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
        """Build a fresh self-model from CapabilityBus owner truth."""
        result = self.capability_bus.request("CAPABILITY_STATUS", {})
        if not result.get("success"):
            return {
                "identity": "DUDE - autonomous computer coworker",
                "capability_status": [],
                "capability_states": {},
                "capabilities_available": [],
                "capabilities_degraded": [],
                "capabilities_unavailable": [],
                "capabilities_inactive": [],
                "capability_bus": "failed",
                "status_error": str(result.get("error") or "")[:180],
                "phase17": {},
                "phase19": {},
            }

        data = result.get("data") or {}
        records = data.get("capabilities") or []
        states = {str(r.get("name")): str(r.get("state") or "UNAVAILABLE")
                  for r in records if isinstance(r, dict)}
        available_states = {"READY", "ACTIVE", "WIRED"}
        return {
            "identity": "DUDE - autonomous computer coworker",
            "capability_status": records,
            "capability_states": states,
            "capabilities_available": [
                r.get("name") for r in records
                if r.get("state") in available_states],
            "capabilities_degraded": [
                r.get("name") for r in records
                if r.get("state") == "DEGRADED"],
            "capabilities_unavailable": [
                r.get("name") for r in records
                if r.get("state") in {"UNAVAILABLE", "FAILED"}],
            "capabilities_inactive": [
                r.get("name") for r in records
                if r.get("state") == "INACTIVE"],
            "capabilities_present": [
                r.get("name") for r in records
                if r.get("present")],
            "capability_bus": "operational",
            "phase17": data.get("phase17") or {},
            "phase19": data.get("phase19") or {},
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

        # Refresh the owner-derived capability truth for every real turn.
        # This prevents construction-time state from becoming a stale self-model.
        self._self_model = self._build_self_model()

        # 1. GET LIVE ENVIRONMENT (if not provided)
        if perception is None:
            perception = self._get_fresh_perception()
        
        # 2. GET ACTIVE GOAL/TASK CONTEXT
        active_goal = self._get_active_goal_context()

        # 2b. PHASE 17: CONTEXT PRIMING (bounded, relevance-first).
        # Runs BEFORE classification so reasoning and planning see the
        # selected context. Advisory only: packs never execute, and the
        # existing classifier/router stay the single authority.
        context_bundle = None
        try:
            context_bundle = self._prime_context(
                transcript, perception, active_goal)
        except Exception as e:
            log.warning(f"phase17 context priming degraded: {e}")
        
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
        # Phase 17: the primed bundle is advisory context for reasoning.
        if context_bundle is not None:
            cognitive_context["context_bundle"] = context_bundle
            try:
                cognitive_context["skill_pack_context"] = [
                    {"id": pid, "score": sc, "why": why}
                    for pid, sc, why in context_bundle.skill_packs]
            except Exception:
                pass
            # Phase 18: who the human is (bounded) + current facts.
            try:
                if context_bundle.profile_summary:
                    cognitive_context["human_profile"] = \
                        context_bundle.profile_summary
            except Exception:
                pass
            # Phase 19: the ONE coherent multimodal workspace. It is
            # context/evidence only: no planning, no action selection.
            try:
                if getattr(context_bundle, "workspace", None) is not None:
                    cognitive_context["shared_workspace"] = \
                        context_bundle.workspace
            except Exception:
                pass
        
        # 5. FAST CLASSIFICATION (semantic, not keyword)
        interpretation = self._classify_intent(cognitive_context)
        
        # 6. ENRICH BASED ON MODE
        interpretation = self._enrich_interpretation(interpretation, cognitive_context)

        # 6b. PHASE 17: attach the bounded context bundle to the result
        # (observability + downstream reasoning; never execution).
        if context_bundle is not None:
            try:
                interpretation.context_bundle = context_bundle
            except Exception:
                pass

        # 7. SPATIAL REFERENCE (Phase 15: structural, never lexical).
        # When the turn's meaning resolves to a visible element — the
        # plan's current step names a target, or the answer comes from
        # the live environment (attention focus) — locate it in the
        # authoritative bus state so the workspace can show it.
        try:
            interpretation = self._maybe_resolve_spatial(
                interpretation, cognitive_context)
        except Exception as e:
            log.warning(f"spatial resolution failed: {e}")

        # 8. HAND CONTEXT (Phase 16: sensor observation, never a
        # command). A stable high-confidence point at a fresh visual
        # element attaches what the hand indicates; reasoning decides
        # relevance, and no path here can address the action system.
        try:
            interpretation = self._maybe_attach_hand(
                interpretation, cognitive_context)
        except Exception as e:
            log.warning(f"hand context failed: {e}")
        
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
                normalized = []
                for fact in facts or []:
                    if isinstance(fact, dict):
                        normalized.append(fact)
                    else:
                        text = str(fact)
                        normalized.append({"content": text, "fact": text})
                return MemoryBundle(
                    facts=normalized,
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
            # Classification already inspected this turn's plan. Re-routing
            # against an active goal here can restore an answer-only plan we
            # deliberately rejected. Missing plans go to normal task planning.
            return interpretation

        elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
            interpretation.perception_focus = "full_screen_with_ocr"

        elif interpretation.intent_kind == CognitiveMode.MEMORY_OPERATION:
            interpretation.required_capabilities = ["episodic_memory", "fact_memory"]

        return interpretation

    def _context_primer(self):
        """Phase 17 primer attached by the runtime (optional)."""
        try:
            return getattr(self, "context_primer", None)
        except Exception:
            return None

    def _prime_context(self, transcript, perception, active_goal):
        """Build the bounded ContextBundle (best effort, may be None)."""
        try:
            primer = self._context_primer()
            if primer is None:
                return None
            bundle = primer.prime(
                goal=transcript or "",
                active_goal=active_goal,
                perception=perception)
            log.info("CONTEXT_PRIMED %s", bundle.describe()[:300])
            return bundle
        except Exception as e:
            log.warning(f"context prime failed: {e}")
            return None

    def _visual_bus(self):
        """Phase 15: authoritative visual state (None when unwired)."""
        try:
            return getattr(self.capability_bus, "visual_state_bus", None)
        except Exception:
            return None

    def _maybe_resolve_spatial(self, interpretation, context):
        """Attach a located referent when the turn means a visible thing.

        Sources of the referring expression (in order):
        1. The execution plan's current step target (COMPUTER_WORK) —
           the planner already decided WHAT matters, we only locate it.
        2. The active goal's current step text (continued work).
        3. The live-environment attention focus (answers about the
           screen: focus control, else active window).
        Pure chatter resolves to nothing -> verbal answer only.
        """
        try:
            from .spatial_workspace import resolve_focus, resolve_named
        except Exception:
            return interpretation
        try:
            bus = self._visual_bus()
            if bus is None:
                return interpretation
            try:
                state = bus.get_fresh()
            except Exception:
                state = None
            if state is None:
                return interpretation

            expression = ""
            plan = getattr(interpretation, "execution_plan", None)
            try:
                steps = getattr(plan, "steps", "") or []
                if steps:
                    idx = 0
                    try:
                        ag = (context or {}).get("active_goal") or {}
                        idx = int(ag.get("step_index", 0) or 0)
                    except Exception:
                        idx = 0
                    step = steps[min(max(idx, 0), len(steps) - 1)]
                    expression = (
                        getattr(step, "target_description", "")
                        or getattr(step, "description", ""))
            except Exception:
                expression = ""
            if not expression:
                try:
                    ag = (context or {}).get("active_goal") or {}
                    expression = ag.get("step", "") or ""
                except Exception:
                    expression = ""

            target = None
            if expression:
                target = resolve_named(expression, state)
                # A plan step that names nothing visible is not a
                # failure: fall back to showing the work focus.
                if target is None and getattr(
                        interpretation, "requires_computer_action", False):
                    target = resolve_focus(state)
            elif interpretation.intent_kind == CognitiveMode.ANSWER_FROM_LIVE_ENV:
                target = resolve_focus(state)

            # No structural referent (e.g. conversational turns): ask
            # the local semantic classifier whether the turn wants
            # something on screen pointed out. No phrase lists — a
            # small local model reads the meaning. Unavailable model
            # or "none" keeps today's verbal-only behavior.
            if target is None and not expression:
                try:
                    from .spatial_intent import classify_spatial_want
                    _tx = ""
                    try:
                        _tx = ((context or {}).get("transcript", "") or "")
                    except Exception:
                        _tx = ""
                    _screen = "%s / %s / focused %s" % (
                        getattr(state, "active_app", "?"),
                        getattr(state, "window_title", "?") or "?",
                        getattr(state, "focused_name", "") or "none")
                    _want = classify_spatial_want(_tx, _screen)
                    if _want == "FOCUS":
                        target = resolve_focus(state)
                    elif _want:
                        target = resolve_named(_want, state)
                except Exception as e:
                    log.warning(f"spatial classifier failed: {e}")

            if target is not None:
                interpretation.spatial_reference = target
                log.info("SPATIAL_REF kind=%s name=%r prov=%s",
                         target.kind, target.name[:40],
                         target.provenance[:60])
            return interpretation
        except Exception as e:
            log.warning(f"spatial resolution failed: {e}")
            return interpretation

    def _maybe_attach_hand(self, interpretation, context):
        """Attach a pointed element when the hand sensor warrants it."""
        try:
            tracker = getattr(self.capability_bus, "hand_tracker", None)
            if tracker is None:
                return interpretation
            bus = self._visual_bus()
            if bus is None:
                return interpretation
            pointed = tracker.pointed_element(bus)
            if pointed is not None:
                interpretation.hand_context = pointed
                log.info("HAND_CTX element=%r prov=%s",
                         pointed.get("element", "?")[:40],
                         pointed.get("provenance", "")[:60])
            return interpretation
        except Exception as e:
            log.warning(f"hand attach failed: {e}")
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
            return self._translate_routing_result(None, context)
        
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
            # Solver failure is not evidence that the user ordered an action.
            return self._translate_routing_result(None, context)

    @staticmethod
    def _request_shape(transcript: str, active_goal=None):
        """Conservative fallback evidence, not a catalogue of capabilities.

        Inspect clause heads, not action words anywhere in a question. Quoted
        text is an argument: `type "what is ..."` is still an order, whereas
        `explain "delete ..."` is not. Keep mixed question/action requests whole.
        The original transcript always reaches the selected brain/planner.
        """
        text = re.sub(
            r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d|\u2018[^\u2019\n]*\u2019|(?<!\w)\'[^\'\n]*\'(?!\w)',
            " quoted_content ", transcript or "").lower()
        prefix = (
            r"^(?:(?:hey|hi|hello|dude|computer|assistant|please|okay|ok|"
            r"so|well|now)\b[,\s]*|(?:can|could|would|will)\s+you\s+|"
            r"(?:i want|i need|i would like|i'd like)\s+you\s+to\s+)"
        )
        action_head = (
            r"^(?:open|launch|start|close|shut|click|press|type|enter|drag|"
            r"scroll|switch|focus|minimi[sz]e|maximi[sz]e|delete|create|"
            r"move|copy|rename|save|capture|screenshot|run|execute|install|"
            r"uninstall|download|upload|send|email|schedule|set|change|"
            r"select|paste|cut|navigate|browse|write|edit|"
            r"build|make|organize|organise|turn|enable|disable)\b"
        )
        question_head = (
            r"^(?:what|who|where|when|why|which|how|is|are|was|were|"
            r"do|does|did|have|has|can|could|would|will|should|"
            r"explain|define|describe|summari[sz]e|calculate|compute|"
            r"tell me|let me know|answer|i wonder|i'd like to know|"
            r"i want to know|i need to know)\b"
        )
        action_requested = informational = False
        low_transcript = (transcript or "").strip().lower()
        screen_work_question = bool(
            re.search(r"\b(check|look|see|find|search|read|review|verify|inspect|"
                      r"show|tell me)\b", low_transcript)
            and re.search(r"\b(screen|portal|website|web site|browser|dashboard|"
                          r"employee|employer|boss|manager|inbox|mail|email|"
                          r"task|tasks|assignment|notification|account|page|job|work)\b",
                          low_transcript)
        )
        # A bare coordinated verb remains inside a how-to question:
        # "how do I open and save ..." is not a request to save anything.
        # Sentence boundaries and explicit request wrappers start a new scope.
        how_to = False
        separator = ""
        for index, clause in enumerate(re.split(
                r"([.!?;,\n]+|\b(?:and|then|but)\b)", text)):
            if index % 2:
                separator = clause
                continue
            clause = clause.strip()
            explicit_wrapper = bool(re.match(
                r"^(?:please\b|(?:can|could|would|will)\s+you\b)", clause))
            if re.search(r"[.!?;\n]", separator) or explicit_wrapper:
                how_to = False
            previous = None
            while clause != previous:
                previous = clause
                clause = re.sub(prefix, "", clause).strip()
            if not clause:
                continue
            if re.match(action_head, clause) and not how_to:
                action_requested = True
            if re.match(question_head, clause):
                informational = True
                how_to = bool(re.search(r"\bhow\b", clause))
            # Goal controls are meaningful only in an existing goal context;
            # unrelated questions must never resume that goal implicitly.
            if active_goal and re.fullmatch(
                    r"(?:continue|resume|retry|pause|stop|cancel|go ahead|"
                    r"do it|try again|yes)(?:\s+(?:it|that|the task|the goal))?",
                    clause):
                action_requested = True
        # A live-screen lookup is operational work even when phrased as a
        # yes/no question. It must reach TaskEngine so DUDE observes the real
        # screen, acts through grounded controls, and verifies the result.
        if screen_work_question:
            action_requested = True
            informational = False
        return action_requested, informational

    def _translate_routing_result(self, routing_result, context: Dict[str, Any]) -> CognitiveInterpretation:
        """Solver provenance is not user intent; inspect what the plan does."""
        plan = getattr(routing_result, "plan", None)
        steps = getattr(plan, "steps", None) or []
        action_types = {
            str(getattr(step, "action_type", "") or "").lower()
            for step in steps
        }
        # These read existing knowledge; they are not desktop work requiring
        # the task engine's act/verify loop. Brain already has memory access.
        answer_actions = {
            "search_knowledge", "recall_about_user", "search_conversation_history",
        }
        has_work = bool(action_types - answer_actions - {"", "execute"})
        action_requested, informational = self._request_shape(
            context["transcript"], context.get("active_goal"))
        requires_action = action_requested or (has_work and not informational)
        # A lexical knowledge match can swallow a quoted question inside an
        # order, or just the first half of a mixed request. Let normal planning
        # handle the FULL original request rather than execute that partial
        # answer-only plan. No permission or verification gate is bypassed.
        if not has_work:
            plan = None
        decision = getattr(routing_result, "decision", None)
        log.info(
            "COGNITIVE_TRANSLATE solver=%s plan_actions=%s explicit_action=%s "
            "informational=%s action=%s",
            getattr(decision, "value", "unavailable"),
            ",".join(sorted(action_types)), action_requested,
            informational, requires_action,
        )
        return CognitiveInterpretation(
            intent_kind=(CognitiveMode.COMPUTER_WORK if requires_action
                         else CognitiveMode.CONVERSE),
            user_goal=context["transcript"],
            desired_outcome=("execute user's request on computer" if requires_action
                             else "respond conversationally"),
            requires_computer_action=requires_action,
            required_capabilities=(["ui_click", "type_text", "open_app"]
                                   if requires_action else []),
            confidence=getattr(routing_result, "confidence", 0.2),
            execution_plan=plan if requires_action else None,
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
