"""Observation Learner - Learning from Continuous Perception (Phase 9E).

Connects continuous perception observations to structured knowledge and
procedure candidates, respecting the verification boundary:

    repeated observation
         ↓
    pattern detection (action/change-gated, never layout-only)
         ↓
    structured semantic knowledge
         ↓
    application/workflow knowledge
         ↓
    candidate reusable procedure
         ↓
    verification gate
         ↓
    trusted procedure only after verified successful execution

CRITICAL: Observation learning must NEVER automatically trust an unverified
observation as a successful executable procedure. A screenshot or observation
alone is NOT proof that a workflow succeeded. Failed, incomplete, ambiguous,
or unverified observations must remain candidates or knowledge records,
not trusted procedures.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .state import (
    ProcedureParameter,
    PerceptionLevel,
)
from .perception_service import PerceptionService, SemanticObservation
from .perception import PerceptionEngine
from .procedure_learner import ProcedureLearner, get_procedure_learner
from .procedure_store import ProcedureStore, get_procedure_store
from .intelligence_router import IntelligenceRouter, get_intelligence_router
from .parameter_extractor import get_parameter_extractor
from core.memory import Memory
from core.config import get_config

log = logging.getLogger(__name__)


@dataclass
class ObservedPattern:
    """A detected repeated semantic pattern from observations."""
    pattern_key: str
    app: str
    window_title: str
    control_sequence: list[str]
    action_types: list[str]
    frequency: int = 1
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    contexts: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    promoted: bool = False
    app_sequence: list[str] = field(default_factory=list)
    workflow_id: Optional[str] = None


@dataclass
class ObservedWorkflow:
    """A candidate workflow derived from repeated observations."""
    name: str
    goal: str
    goal_type: str
    app: str
    apps: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    parameters: list[ProcedureParameter] = field(default_factory=list)
    observation_count: int = 0
    confidence: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source_observation_keys: list[str] = field(default_factory=list)
    workflow_id: Optional[str] = None
    app_sequence: list[str] = field(default_factory=list)


class PatternDetector:
    """Detects repeated semantic patterns in observation sequences."""

    def __init__(self, min_frequency: int = 3, similarity_threshold: float = 0.7,
                 pattern_store=None):
        self.min_frequency = min_frequency
        self.similarity_threshold = similarity_threshold
        self._patterns: dict[str, ObservedPattern] = {}
        # RLock: add_observation holds the lock while _check_pattern
        # re-enters it; a plain Lock deadlocks on the first observation.
        self._lock = threading.RLock()
        self._recent_observations: list[SemanticObservation] = []
        self._max_recent = 100
        self._pattern_store = pattern_store
        self._recent_apps: list[str] = []
        self._max_app_history = 5

    def add_observation(self, obs: SemanticObservation,
                        goal: Optional[str] = None) -> None:
        """Add an observation and check for patterns."""
        self._current_goal_hint = goal
        with self._lock:
            self._recent_observations.append(obs)
            if len(self._recent_observations) > self._max_recent:
                self._recent_observations.pop(0)

            if obs.active_app and obs.active_app != "unknown":
                if not self._recent_apps or self._recent_apps[-1] != obs.active_app:
                    self._recent_apps.append(obs.active_app)
                    if len(self._recent_apps) > self._max_app_history:
                        self._recent_apps.pop(0)

            if obs.control_types:
                control_seq = self._extract_control_sequence(obs)
                if control_seq:
                    self._check_pattern(
                        obs, control_seq,
                        goal=getattr(self, "_current_goal_hint", None))

    def _extract_control_sequence(self, obs: SemanticObservation) -> list[str]:
        """Extract a normalized control sequence from an observation."""
        return list(obs.control_types)

    @staticmethod
    def _control_identity(c) -> str:
        """Stable identity for a control (AutomationId preferred)."""
        try:
            aid = (getattr(c, "automation_id", "") or "").strip()
        except Exception:
            aid = ""
        if aid:
            return "aid:" + aid
        try:
            return "tn:%s|%s" % (getattr(c, "ctype", "") or "",
                                 getattr(c, "name", "") or "")
        except Exception:
            return "tn:|"

    def _check_pattern(self, obs: SemanticObservation, control_seq: list[str],
                         goal: Optional[str] = None) -> None:
        """Check if this control sequence matches an existing pattern."""
        pattern_key = f"{obs.active_app}|{obs.window_title}|{'->'.join(control_seq)}"
        goal = goal if isinstance(goal, str) and goal.strip() else None

        with self._lock:
            if pattern_key in self._patterns:
                pattern = self._patterns[pattern_key]
                pattern.frequency += 1
                pattern.last_seen = time.time()
                pattern.contexts.append({
                    "app": obs.active_app,
                    "window": obs.window_title,
                    "focused": obs.focused_name,
                    "focused_ctype": obs.focused_ctype,
                    "dialog": obs.dialog_kind,
                    "change": obs.change,
                    "delta": obs.delta_summary(),
                    "timestamp": obs.captured_at,
                    "goal": goal,
                })
                if obs.active_app and obs.active_app not in pattern.app_sequence:
                    pattern.app_sequence.append(obs.active_app)
                pattern.confidence = min(0.9, 0.3 + (pattern.frequency * 0.15))
                self._persist_pattern(pattern)
            else:
                pattern = ObservedPattern(
                    pattern_key=pattern_key,
                    app=obs.active_app,
                    window_title=obs.window_title,
                    control_sequence=control_seq,
                    action_types=self._infer_action_types(obs),
                    frequency=1,
                    contexts=[{
                        "app": obs.active_app,
                        "window": obs.window_title,
                        "focused": obs.focused_name,
                        "focused_ctype": obs.focused_ctype,
                        "dialog": obs.dialog_kind,
                        "change": obs.change,
                        "delta": obs.delta_summary(),
                        "timestamp": obs.captured_at,
                        "goal": goal,
                    }],
                    confidence=0.3,
                    app_sequence=self._recent_apps.copy(),
                )
                self._patterns[pattern_key] = pattern
                self._persist_pattern(pattern)

    def _persist_pattern(self, pattern: ObservedPattern) -> None:
        """Persist pattern to PatternStore for cross-session persistence."""
        if not getattr(self, "_pattern_store", None):
            return
        try:
            from .pattern_store import PersistedPattern
            persisted = PersistedPattern(
                pattern_key=pattern.pattern_key,
                app=pattern.app,
                window_title=pattern.window_title,
                control_sequence=pattern.control_sequence,
                action_types=pattern.action_types,
                frequency=pattern.frequency,
                first_seen=pattern.first_seen,
                last_seen=pattern.last_seen,
                contexts=pattern.contexts[-10:],
                confidence=pattern.confidence,
                promoted=pattern.promoted,
                app_sequence=pattern.app_sequence,
                workflow_id=pattern.workflow_id,
            )
            self._pattern_store.save(persisted)
        except Exception as e:
            log.warning(f"Failed to persist pattern: {e}")

    def _infer_action_types(self, obs: SemanticObservation) -> list[str]:
        """Infer likely action types from observation context."""
        actions = []
        if obs.dialog_kind not in ("none", ""):
            actions.append("dialog_interaction")
        if obs.focused_name:
            if "edit" in obs.focused_ctype.lower() or "document" in obs.focused_ctype.lower():
                actions.append("text_input")
            elif "button" in obs.focused_ctype.lower():
                actions.append("click")
            elif "menu" in obs.focused_ctype.lower():
                actions.append("menu_select")
        if obs.change in ("action_relevant", "task_relevant", "recovery_relevant"):
            actions.append("navigation")
        return actions

    def get_ready_patterns(self) -> list[ObservedPattern]:
        """Patterns ready for promotion.

        A repeated control layout alone is an observed STATE, not proof
        of a performed action. Promotion additionally requires evidence
        of action/change: inferred action types plus a relevant change,
        dialog, or control delta in at least one context.
        """
        def _has_action_evidence(p: ObservedPattern) -> bool:
            if not p.action_types:
                return False
            for ctx in p.contexts:
                if (ctx.get("dialog") or "none") not in ("none", ""):
                    return True
                if (ctx.get("change") or "") in (
                        "action_relevant", "task_relevant",
                        "recovery_relevant"):
                    return True
                if ctx.get("delta") and ctx.get("delta") != "no-change":
                    return True
            return False

        with self._lock:
            return [
                p for p in self._patterns.values()
                if p.frequency >= self.min_frequency
                and not p.promoted
                and p.confidence >= 0.45
                and _has_action_evidence(p)
            ]

    def mark_promoted(self, pattern_key: str) -> None:
        """Mark a pattern as promoted to candidate."""
        with self._lock:
            if pattern_key in self._patterns:
                self._patterns[pattern_key].promoted = True
                if self._pattern_store:
                    try:
                        self._pattern_store.mark_promoted(pattern_key)
                    except Exception:
                        pass


class WorkflowCandidateBuilder:
    """Builds workflow candidates from promoted patterns."""

    def __init__(self):
        self._extractor = get_parameter_extractor()

    def build_candidate(self, pattern: ObservedPattern) -> Optional[ObservedWorkflow]:
        """Build a workflow candidate from a promoted pattern."""
        return self._build_workflow(pattern)

    def build_multi_app_candidate(self, pattern: ObservedPattern) -> Optional[ObservedWorkflow]:
        """Build a candidate spanning multiple apps."""
        return self._build_workflow(pattern, is_multi_app=True)

    def _build_workflow(self, pattern: ObservedPattern,
                        is_multi_app: bool = False) -> Optional[ObservedWorkflow]:
        """Build a workflow candidate from a promoted pattern."""
        _ = is_multi_app
        if not pattern.control_sequence:
            return None

        steps = []
        for i, ctrl_type in enumerate(pattern.control_sequence):
            action_type = self._infer_action_for_control(pattern, i)
            steps.append({
                "description": f"Interact with {ctrl_type}",
                "intent": f"Step {i+1} in observed workflow",
                "action_type": action_type,
                "target": ctrl_type,
                "expected": "",
                "verification_method": self._infer_verification_method(action_type),
                "parameters": {},
            })

        parameters = self._extract_parameters(pattern)
        goal = self._generate_goal(pattern)
        apps = pattern.app_sequence if len(pattern.app_sequence) > 1 else [pattern.app]

        return ObservedWorkflow(
            name=f"observed_workflow_{pattern.app}_{int(time.time())}",
            goal=goal,
            goal_type="AUTOMATE",
            app=pattern.app,
            apps=apps,
            steps=steps,
            parameters=parameters,
            observation_count=pattern.frequency,
            confidence=pattern.confidence,
            source_observation_keys=[pattern.pattern_key],
            workflow_id=pattern.workflow_id,
            app_sequence=list(pattern.app_sequence),
        )

    def _infer_action_for_control(self, pattern: ObservedPattern, index: int) -> str:
        """Infer action type for a control in the sequence."""
        if index < len(pattern.action_types):
            return pattern.action_types[index]
        ctrl = pattern.control_sequence[index] if index < len(pattern.control_sequence) else ""
        if "edit" in ctrl.lower() or "document" in ctrl.lower():
            return "type_text"
        elif "button" in ctrl.lower():
            return "click"
        elif "menu" in ctrl.lower():
            return "menu_select"
        elif "tab" in ctrl.lower():
            return "click"
        elif "list" in ctrl.lower() or "tree" in ctrl.lower():
            return "click"
        return "click"

    def _infer_verification_method(self, action_type: str) -> str:
        """Infer verification method from action type."""
        mapping = {
            "click": "UIA_STATE_CHANGE",
            "type_text": "OCR_TEXT_APPEARED",
            "text_input": "OCR_TEXT_APPEARED",
            "menu_select": "UIA_STATE_CHANGE",
            "dialog_interaction": "WINDOW_APPEARED",
            "navigation": "WINDOW_APPEARED",
        }
        return mapping.get(action_type, "CUSTOM")

    def _extract_parameters(self, pattern: ObservedPattern) -> list[ProcedureParameter]:
        """Extract parameter definitions using ParameterExtractor."""
        params: list[ProcedureParameter] = []
        seen: set[str] = set()

        goal_text = self._generate_goal(pattern)
        if self._extractor:
            try:
                extraction = self._extractor.extract(goal_text, [])
            except Exception:
                extraction = None
            if extraction:
                for param_name, value in extraction.parameters.items():
                    if isinstance(value, str) and value and param_name not in seen:
                        seen.add(param_name)
                        params.append(ProcedureParameter(
                            name=param_name,
                            type="integer" if value.isdigit() else "string",
                            required=True,
                            description=f"Parameter extracted from observation goal: {param_name}",
                            default=value,
                        ))

        for ctx in pattern.contexts:
            window_title = ctx.get("window", "")
            if not window_title:
                continue
            try:
                extraction = self._extractor.extract(window_title, [])
            except Exception:
                continue
            for param_name, value in extraction.parameters.items():
                if isinstance(value, str) and value and param_name not in seen:
                    seen.add(param_name)
                    ptype = "string"
                    if value.isdigit():
                        ptype = "integer"
                    elif param_name in ("filename", "path", "file_path", "folder_name"):
                        ptype = "filename"
                    params.append(ProcedureParameter(
                        name=param_name,
                        type=ptype,
                        required=True,
                        description=f"Parameter extracted from window title: {param_name}",
                        default=value,
                    ))

        # Cross-goal generalization: the same structural pattern seen
        # under different task goals with the same parameter names but
        # different values becomes a true template parameter (no default),
        # so the learned representation stays reusable and never freezes
        # one observed literal as the permanent value.
        goals = [c.get("goal") for c in pattern.contexts
                 if isinstance(c.get("goal"), str) and c.get("goal").strip()]
        if len(set(goals)) >= 2 and self._extractor:
            by_name: dict[str, set[str]] = {}
            for g in goals:
                try:
                    eg = self._extractor.extract(g, [])
                except Exception:
                    continue
                for pn, pv in eg.parameters.items():
                    if isinstance(pv, str) and pv:
                        by_name.setdefault(pn, set()).add(pv)
            for pn, values in by_name.items():
                if len(values) >= 2 and pn not in seen:
                    seen.add(pn)
                    sample = sorted(values)[0]
                    params.append(ProcedureParameter(
                        name=pn,
                        type="integer" if sample.isdigit() else "string",
                        required=True,
                        description=f"Generalized from {len(values)} observed values "
                                    f"({', '.join(sorted(values)[:4])})",
                        default=None,
                    ))

        titles = [ctx.get("window", "") for ctx in pattern.contexts]
        if titles:
            common_prefix = self._common_prefix(titles)
            common_suffix = self._common_suffix(titles)
            if common_prefix and common_prefix != titles[0] \
                    and "window_title_prefix" not in seen:
                params.append(ProcedureParameter(
                    name="window_title_prefix",
                    type="string",
                    required=False,
                    description="Variable prefix in window title",
                    default=common_prefix,
                ))
            if common_suffix and common_suffix != titles[0] \
                    and "window_title_suffix" not in seen:
                params.append(ProcedureParameter(
                    name="window_title_suffix",
                    type="string",
                    required=False,
                    description="Variable suffix in window title",
                    default=common_suffix,
                ))
        return params

    def _common_prefix(self, strings: list[str]) -> str:
        """Find common prefix among strings."""
        if not strings:
            return ""
        prefix = strings[0]
        for s in strings[1:]:
            while not s.startswith(prefix) and prefix:
                prefix = prefix[:-1]
        return prefix

    def _common_suffix(self, strings: list[str]) -> str:
        """Find common suffix among strings."""
        if not strings:
            return ""
        suffix = strings[0]
        for s in strings[1:]:
            while not s.endswith(suffix) and suffix:
                suffix = suffix[1:]
        return suffix

    def _generate_goal(self, pattern: ObservedPattern) -> str:
        """Generate a natural language goal from pattern."""
        actions = " -> ".join(pattern.action_types[:3])
        return f"In {pattern.app}, {actions}"


class ObservationLearner:
    """Main coordinator for learning from continuous perception observations.

    Connects PerceptionService, PatternDetector, WorkflowCandidateBuilder,
    ProcedureLearner, IntelligenceRouter, KnowledgeBase, PatternStore and
    the Associations graph.
    """

    def __init__(
        self,
        perception_service: PerceptionService,
        perception_engine: PerceptionEngine,
        memory: Memory,
        procedure_store: Optional[ProcedureStore] = None,
        procedure_learner: Optional[ProcedureLearner] = None,
        intelligence_router: Optional[IntelligenceRouter] = None,
        enable_learning: bool = False,
    ):
        self.perception_service = perception_service
        self.perception_engine = perception_engine
        self.memory = memory
        self.procedure_store = procedure_store or get_procedure_store()
        self.procedure_learner = procedure_learner or get_procedure_learner()
        self.intelligence_router = intelligence_router or get_intelligence_router()

        from .pattern_store import get_pattern_store
        self._pattern_store = get_pattern_store()
        self._detector = PatternDetector(pattern_store=self._pattern_store)
        self._builder = WorkflowCandidateBuilder()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._processed_count = 0
        self._processed_keys: set = set()
        self._current_workflow_id: Optional[str] = None

        try:
            cfg = get_config()
            self.enable_learning = cfg.get(
                "orchestrator", "enable_observation_learning",
                default=enable_learning)
            self.min_pattern_frequency = cfg.get(
                "orchestrator", "min_observation_frequency", default=3)
            self.max_candidates_per_session = cfg.get(
                "orchestrator", "max_observation_candidates", default=10)
        except Exception as e:
            log.warning(f"Failed to load observation learner config: {e}")
            self.enable_learning = enable_learning
            self.min_pattern_frequency = 3
            self.max_candidates_per_session = 10

        try:
            from core.associations import get_associations
            self._associations = get_associations()
        except Exception:
            self._associations = None

        log.info(f"ObservationLearner initialized: enabled={self.enable_learning}, "
                 f"min_frequency={self.min_pattern_frequency}")

    def start(self) -> None:
        """Start the observation learning loop."""
        if self._running:
            return
        if not self.enable_learning:
            log.info("Observation learning disabled by config")
            return
        self._running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="du-obslearner")
        self._thread.start()
        log.info("ObservationLearner started")

    def stop(self) -> None:
        """Stop the observation learning loop."""
        self._running = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        """Main learning loop - processes new observations."""
        while not self._stop.wait(timeout=5.0):
            try:
                if not self.enable_learning:
                    continue
                self._process_new_observations()
                self._check_patterns()
            except Exception as e:
                log.warning(f"ObservationLearner loop error: {e}")

    def _process_new_observations(self, goal: Optional[str] = None) -> None:
        """Process new observations from PerceptionService."""
        recent = self.perception_service.observations.recent(n=20)
        for obs in recent:
            if self._is_processed(obs):
                continue
            self._detector.add_observation(obs, goal=goal)
            self._mark_processed(obs)
            self._processed_count += 1

    def _is_processed(self, obs: SemanticObservation) -> bool:
        """Check if observation was already processed."""
        try:
            return obs.key() in self._processed_keys
        except Exception:
            return False

    def _mark_processed(self, obs: SemanticObservation) -> None:
        try:
            self._processed_keys.add(obs.key())
        except Exception:
            pass
        if len(self._processed_keys) > 1000:
            self._processed_keys.clear()

    def _check_patterns(self) -> None:
        """Check for patterns ready to become candidates."""
        ready = self._detector.get_ready_patterns()
        for pattern in ready:
            if pattern.frequency >= self.min_pattern_frequency and not pattern.promoted:
                self._promote_pattern(pattern)

    def process_pending(self, max_observations: int = 20,
                        goal: Optional[str] = None) -> dict:
        """Synchronously drain new observations (deterministic, for tests)."""
        before = self._processed_count
        self._process_new_observations(goal=goal)
        self._check_patterns()
        after = self._processed_count
        return {
            "processed_before": before,
            "processed_after": after,
            "processed_delta": after - before,
            "patterns_detected": len(self._detector._patterns),
            "candidates_ready": len(self._detector.get_ready_patterns()),
        }

    def correlate_recent_patterns(self, window_seconds: float = 180.0) -> dict:
        """Correlate recent patterns across apps into one workflow identity."""
        now = time.time()
        with self._detector._lock:
            recent = [p for p in self._detector._patterns.values()
                      if now - p.last_seen <= window_seconds]
        apps: list[str] = []
        for p in recent:
            for a in [p.app] + list(p.app_sequence or []):
                if a and a != "unknown" and a not in apps:
                    apps.append(a)
        keys = [p.pattern_key for p in recent]
        if len(apps) < 2 or not keys:
            return {"correlated": False, "apps": apps, "keys": keys,
                    "workflow_id": None}
        workflow_id = str(uuid.uuid4())
        for p in recent:
            p.workflow_id = workflow_id
            if self._detector._pattern_store:
                try:
                    self._detector._pattern_store.update_workflow_id(
                        [p.pattern_key], workflow_id)
                except Exception:
                    pass
        try:
            if self.memory is not None:
                self.memory.remember_fact(
                    f"[observation {datetime.now().strftime('%Y-%m-%d %H:%M')}] "
                    f"correlated workflow {workflow_id}: "
                    f"{' -> '.join(apps)} ({len(keys)} patterns)",
                    category="observation_workflow")
        except Exception:
            pass
        return {"correlated": True, "apps": apps, "keys": keys,
                "workflow_id": workflow_id,
                "observations": sum(p.frequency for p in recent)}

    def get_context_for_app(self, app: str, limit: int = 8) -> list[str]:
        """Retrieve stored semantic relationships for planning context."""
        out: list[str] = []
        try:
            if self._associations is not None:
                out.extend(self._associations.associations_for(app)[:limit])
        except Exception:
            pass
        try:
            if self.memory is not None:
                for f in self.memory.recall_facts(app, limit=limit):
                    out.append(str(f)[:200])
        except Exception:
            pass
        return out[:limit]

    def _promote_pattern(self, pattern: ObservedPattern) -> None:
        """Promote a detected pattern to a workflow candidate."""
        try:
            workflow_id = pattern.workflow_id or str(uuid.uuid4())
            pattern.workflow_id = workflow_id

            if len(pattern.app_sequence) > 1:
                workflow = self._builder.build_multi_app_candidate(pattern)
            else:
                workflow = self._builder.build_candidate(pattern)

            if not workflow:
                return

            workflow.workflow_id = workflow_id
            workflow.app_sequence = list(pattern.app_sequence)

            self._store_as_knowledge(workflow)
            self._persist_pattern(pattern)
            self._record_associations(workflow)
            self._create_procedure_candidate(workflow)
            self._detector.mark_promoted(pattern.pattern_key)

            log.info(f"Promoted observation pattern to workflow candidate: {workflow.name} "
                     f"(freq={pattern.frequency}, confidence={pattern.confidence:.2f}, "
                     f"apps={workflow.apps})")
        except Exception as e:
            log.warning(f"Failed to promote pattern: {e}")

    def _persist_pattern(self, pattern: ObservedPattern) -> None:
        """Persist pattern to PatternStore for cross-session persistence."""
        store = getattr(self._detector, "_pattern_store", None)
        if not store:
            return
        try:
            from .pattern_store import PersistedPattern
            persisted = PersistedPattern(
                pattern_key=pattern.pattern_key,
                app=pattern.app,
                window_title=pattern.window_title,
                control_sequence=pattern.control_sequence,
                action_types=pattern.action_types,
                frequency=pattern.frequency,
                first_seen=pattern.first_seen,
                last_seen=pattern.last_seen,
                contexts=pattern.contexts[-10:],
                confidence=pattern.confidence,
                promoted=pattern.promoted,
                app_sequence=pattern.app_sequence,
                workflow_id=pattern.workflow_id,
            )
            store.save(persisted)
        except Exception as e:
            log.warning(f"Failed to persist pattern: {e}")

    def _record_associations(self, workflow: ObservedWorkflow) -> None:
        """Record semantic associations in the Associations graph."""
        if not self._associations:
            return
        try:
            for app in workflow.apps:
                try:
                    self._associations.record_analytics(
                        app=app,
                        window=workflow.goal,
                        ocr_text="",
                        viewer_out=f"workflow:{workflow.name}"
                    )
                except Exception:
                    pass
            for app in workflow.apps:
                try:
                    self._associations._link(
                        app, workflow.name,
                        rel="has_workflow",
                        delta=workflow.confidence)
                except Exception:
                    pass
            for step in workflow.steps:
                try:
                    self._associations._link(
                        workflow.name, step.get("target", ""),
                        rel="has_step",
                        delta=workflow.confidence)
                except Exception:
                    pass
            try:
                self._associations.record_insight(
                    f"workflow {workflow.name}: "
                    f"{' -> '.join(workflow.apps)} | "
                    f"{'; '.join(s.get('action_type', '') for s in workflow.steps[:6])}",
                    kind="workflow_step",
                    basis="observation")
            except Exception:
                pass
            log.info(f"Recorded associations for workflow: {workflow.name}")
        except Exception as e:
            log.warning(f"Failed to record associations: {e}")

    def _store_as_knowledge(self, workflow: ObservedWorkflow) -> None:
        """Store workflow as structured semantic knowledge in Memory."""
        try:
            from core.knowledge import get_knowledge
            kb = get_knowledge()

            fact_lines = [
                f"Observed workflow: {workflow.name}",
                f"Goal: {workflow.goal}",
                f"Apps: {', '.join(workflow.apps)}",
                f"Steps: {len(workflow.steps)}",
                f"Confidence: {workflow.confidence:.2f}",
                f"Observations: {workflow.observation_count}",
            ]
            for i, step in enumerate(workflow.steps):
                fact_lines.append(
                    f"  Step {i+1}: {step['action_type']} -> {step['target']}")

            fact = f"[observation {datetime.now().strftime('%Y-%m-%d %H:%M')}] " \
                + "; ".join(fact_lines)

            self.memory.remember_fact(fact, category="observation_workflow")

            kb.save_note(
                f"Observed Workflow: {workflow.name}",
                "\n".join(fact_lines),
                filename=f"obs_workflow_{workflow.name.replace(' ', '_')}.md",
            )

            log.info(f"Stored observation-derived knowledge: {workflow.name}")
        except Exception as e:
            log.warning(f"Failed to store knowledge: {e}")

    def _create_procedure_candidate(self, workflow: ObservedWorkflow) -> None:
        """Create a procedure candidate requiring verification.

        IMPORTANT: This creates a CANDIDATE only. It does NOT become a trusted
        procedure until verified through successful execution.
        """
        try:
            steps = []
            for step in workflow.steps:
                step_data = {
                    "description": step.get("description", ""),
                    "intent": step.get("intent", ""),
                    "action_type": step.get("action_type", "execute"),
                    "target": step.get("target", ""),
                    "expected": step.get("expected", ""),
                    "verification_method": step.get("verification_method", "CUSTOM"),
                }
                if step.get("parameters"):
                    step_data["parameters"] = step["parameters"]
                steps.append(step_data)

            from .procedure_learner import LearningCandidate

            candidate_obj = LearningCandidate(
                goal=workflow.goal,
                goal_type=workflow.goal_type,
                context={
                    "apps": workflow.apps,
                    "source": "observation",
                    "confidence": workflow.confidence,
                    "observation_count": workflow.observation_count,
                    "workflow_id": workflow.workflow_id,
                },
                steps=steps,
                parameters=list(workflow.parameters),
                verification_results=[],
                perception_requirements=[1],
                source_task_id=f"obs_{int(time.time())}",
            )

            self.procedure_learner._candidates[workflow.name] = candidate_obj
            self.procedure_learner._candidates[workflow.name].execution_traces = [{
                "goal": workflow.goal,
                "steps": steps,
                "parameters": {p.name: p.default for p in workflow.parameters
                               if p.default},
            }]

            log.info(f"Created observation-derived procedure candidate: {workflow.name} "
                     f"(requires verification before promotion)")
        except Exception as e:
            log.warning(f"Failed to create procedure candidate: {e}")

    def get_stats(self) -> dict:
        """Get learning statistics."""
        return {
            "enabled": self.enable_learning,
            "running": self._running,
            "processed_observations": self._processed_count,
            "patterns_detected": len(self._detector._patterns),
            "candidates_ready": len(self._detector.get_ready_patterns()),
            "patterns_promoted": sum(
                1 for p in self._detector._patterns.values() if p.promoted),
        }

    def get_patterns(self) -> list[dict]:
        """Get all detected patterns for inspection."""
        return [
            {
                "key": p.pattern_key,
                "app": p.app,
                "window": p.window_title,
                "frequency": p.frequency,
                "confidence": p.confidence,
                "promoted": p.promoted,
                "actions": p.action_types,
            }
            for p in self._detector._patterns.values()
        ]


def get_observation_learner(
    perception_service: Optional[PerceptionService] = None,
    perception_engine: Optional[PerceptionEngine] = None,
    memory: Optional[Memory] = None,
    procedure_store: Optional[ProcedureStore] = None,
    procedure_learner: Optional[ProcedureLearner] = None,
    intelligence_router: Optional[IntelligenceRouter] = None,
    enable_learning: bool = False,
) -> ObservationLearner:
    """Factory to create an ObservationLearner instance."""
    return ObservationLearner(
        perception_service=perception_service,
        perception_engine=perception_engine,
        memory=memory,
        procedure_store=procedure_store,
        procedure_learner=procedure_learner,
        intelligence_router=intelligence_router,
        enable_learning=enable_learning,
    )
