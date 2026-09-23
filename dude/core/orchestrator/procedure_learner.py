"""Procedure Learner - Automatic Procedure Learning from Verified Execution Traces.

Implements a conservative, verification-gated learning pipeline:
- Creates candidates from successful, verified task traces
- Generalizes parameters across multiple successful executions
- Promotes candidates through: Candidate -> Verified -> Trusted
- Only verified, successful executions create candidates
- Failures are recorded for recovery intelligence, never promoted
- Secrets are never stored in procedures
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .state import (
    Procedure,
    ProcedureParameter,
    TaskState,
    TaskType,
    SubGoal,
    VerificationMethod,
    RiskLevel,
    VerificationResult,
    PerceptionSnapshot,
    PerceptionLevel,
    OrchestratorState,
)
from .procedure_store import ProcedureStore, get_procedure_store, ProcedureCandidate
from .parameter_extractor import get_parameter_extractor
from .parameter_binder import ParameterBinder
from core.memory import Memory

log = logging.getLogger(__name__)


def _normalize_candidate_key(goal: str, goal_type: str) -> str:
    """Normalize a goal to a candidate key by replacing recognized parameters with placeholders.
    
    Uses the parameter extractor to identify parameterizable parts of the goal.
    """
    try:
        from .parameter_extractor import get_parameter_extractor
        extractor = get_parameter_extractor()
        extraction = extractor.extract(goal, None)
        
        normalized = goal
        # Positional arithmetic mask FIRST ("calculate 25 times 4" and
        # "calculate 8 + 8" share one shape): value-wise replacement
        # below cannot collapse identical operands ("8 + 8" would eat
        # operand2). Positional masking unifies all such goals.
        normalized = re.sub(
            r'\b(calculate|compute|work\s+out|evaluate|solve)\s+'
            r'\S+\s+\S+\s+\S+',
            r'\1 {operand1} {operator} {operand2}', normalized,
            flags=re.IGNORECASE)
        # Replace extracted parameter values with placeholders — but
        # only where the value appears OUTSIDE existing placeholders.
        # The mask above already emits {operand1} etc.; a naive replace
        # would corrupt them ("8" inside "{operand1}" -> "{operand{...}}").
        for param_name, value in extraction.parameters.items():
            if not (isinstance(value, str) and value):
                continue
            bare = re.sub(r'\{[^}]*\}', '', normalized)
            if value in bare:
                normalized = normalized.replace(value, f"{{{param_name}}}")
        
        return f"{goal_type}:{normalized}"
    except Exception:
        # NOTE: do NOT `import re` here — any import inside this function
        # scope would make `re` local and break the try block above
        # (UnboundLocalError). Module top already imports re.
        # Fallback: simple heuristic replacement (module-top `re`).
        normalized = goal
        # Replace quoted strings
        normalized = re.sub(r'["\']([^"\']+)["\']', r'{\1}', normalized)
        # Replace common patterns like "folder X" -> "folder {folder_name}"
        normalized = re.sub(r'\bfolder\s+([a-zA-Z0-9_\-\.]+)', r'folder {folder_name}', normalized)
        normalized = re.sub(r'\bdirectory\s+([a-zA-Z0-9_\-\.]+)', r'directory {folder_name}', normalized)
        normalized = re.sub(r'\bfile\s+([a-zA-Z0-9_\-\.]+)', r'file {file_path}', normalized)
        return f"{goal_type}:{normalized}"


@dataclass
class LearningCandidate:
    """A procedure candidate built from a successful, verified task trace."""
    goal: str
    goal_type: str
    context: dict
    steps: list[dict]
    parameters: list[ProcedureParameter]
    verification_results: list[dict]
    perception_requirements: list[int]
    source_task_id: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    success_count: int = 1
    failure_count: int = 0
    
    def to_candidate(self) -> "ProcedureCandidate":
        """Convert to ProcedureCandidate for promotion."""
        from .procedure_store import ProcedureCandidate
        return ProcedureCandidate(
            goal=self.goal,
            context=self.context,
            steps=self.steps,
            verification_results=self.verification_results,
        )


@dataclass
class GeneralizationResult:
    """Result of generalizing parameters across multiple executions."""
    generalized_parameters: list[ProcedureParameter]
    generalized_steps: list[dict]
    confidence: float
    matched_executions: int


class SecretSanitizer:
    """Sanitizes sensitive data from learning traces."""
    
    # Patterns that indicate sensitive data - (pattern, secret_type)
    # Each pattern should have TWO capture groups: (key_name, value)
    SECRET_PATTERNS = [
        (re.compile(r'(?i)(password|passwd|pwd|secret)\s*[:=]\s*["\']?([^\s"\']+)', re.IGNORECASE), 'password'),
        (re.compile(r'(?i)(api[_-]?key|secret[_-]?key)\s*[:=]\s*["\']?([^\s"\']+)', re.IGNORECASE), 'api_key'),
        (re.compile(r'(?i)(bearer|authorization)\s+([a-zA-Z0-9_\-\.]+)', re.IGNORECASE), 'bearer_token'),
        (re.compile(r'(?i)(private[_-]?key|secret[_-]?key)\s*[:=]\s*["\']?([^\s"\']+)', re.IGNORECASE), 'private_key'),
        (re.compile(r'(?i)(token|auth[_-]?token|access[_-]?token)\s*[:=]\s*["\']?([^\s"\']+)', re.IGNORECASE), 'password'),
    ]
    
    SAFE_REPLACEMENTS = {
        'password': '<REDACTED_PASSWORD>',
        'api_key': '<REDACTED_API_KEY>',
        'bearer_token': '<REDACTED_BEARER_TOKEN>',
        'private_key': '<REDACTED_PRIVATE_KEY>',
    }
    
    def __init__(self):
        self._patterns = [(p, t) for p, t in self.SECRET_PATTERNS]
    
    def sanitize(self, text: str) -> str:
        """Sanitize sensitive data from text."""
        if not isinstance(text, str):
            return text
        result = text
        for pattern, secret_type in self._patterns:
            # Replace the VALUE (group 2) with the replacement, keeping the key (group 1)
            def repl(m):
                return f"{m.group(1)}={self.SAFE_REPLACEMENTS.get(secret_type, '<REDACTED>')}"
            result = pattern.sub(repl, result)
        return result
    
    def sanitize_dict(self, data: dict) -> dict:
        """Recursively sanitize dictionary values."""
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                # Check if key name suggests a secret
                kl = k.lower()
                if any(kl == secret or kl.endswith('_' + secret) for secret in ['password', 'passwd', 'pwd', 'secret', 'token', 'private_key', 'secret_key']):
                    result[k] = self.SAFE_REPLACEMENTS.get('password', '<REDACTED>')
                elif 'api' in kl and 'key' in kl:
                    result[k] = self.SAFE_REPLACEMENTS.get('api_key', '<REDACTED_API_KEY>')
                else:
                    result[k] = self.sanitize(v)
            elif isinstance(v, dict):
                result[k] = self.sanitize_dict(v)
            elif isinstance(v, list):
                result[k] = [self.sanitize(item) if isinstance(item, str) else item for item in v]
            else:
                result[k] = v
        return result
    
    def contains_secrets(self, text: str) -> bool:
        """Check if text contains potential secrets."""
        if not isinstance(text, str):
            return False
        for pattern, _ in self._patterns:
            if pattern.search(text):
                return True
        return False


class ProcedureGeneralizer:
    """Generalizes parameters across multiple successful executions of the same procedure."""
    
    def __init__(self, min_occurrences: int = 2):
        self.min_occurrences = min_occurrences
        self._extractor = get_parameter_extractor()
    
    def generalize(
        self,
        executions: list[dict],  # List of successful execution traces
        procedure: Procedure
    ) -> GeneralizationResult:
        """Generalize parameters across multiple executions."""
        if len(executions) < self.min_occurrences:
            return GeneralizationResult(
                generalized_parameters=procedure.parameters,
                generalized_steps=procedure.steps,
                confidence=procedure.confidence,
                matched_executions=len(executions)
            )
        
        # Extract parameter values from each execution
        param_values: dict[str, list[Any]] = {}
        for exec_trace in executions:
            # Extract from the parameters field of the execution trace
            if "parameters" in exec_trace and isinstance(exec_trace["parameters"], dict):
                for param_name, value in exec_trace["parameters"].items():
                    if param_name not in param_values:
                        param_values[param_name] = []
                    param_values[param_name].append(value)
        
        # Generalize parameters that vary across executions
        generalized_params = []
        for param in procedure.parameters:
            values = param_values.get(param.name, [])
            if len(set(str(v) for v in values)) > 1:
                # Parameter varies - make it a template parameter (no default)
                generalized_params.append(ProcedureParameter(
                    name=param.name,
                    type=param.type,
                    required=param.required,
                    description=f"Template parameter (generalized from {len(set(str(v) for v in values))} values)",
                    default=None,  # No default for template parameters
                    constraints=param.constraints,
                ))
            else:
                generalized_params.append(param)
        
        # Generalize steps - replace concrete values with parameter placeholders.
        # Exactly one output step per input step: the first matching
        # parameter wins (the old code appended once per matching
        # parameter, duplicating steps). Values shorter than 3 chars
        # never rewrite targets: a value like 'B' would corrupt any
        # target merely containing that letter.
        generalized_steps = []
        for step in procedure.steps:
            gen_step = step.copy()
            # Replace concrete values in target with parameter placeholders
            # Also update step's parameters dict with generalized parameter info
            generalized_step_params = {}
            replaced = False
            for param in procedure.parameters:
                if replaced:
                    break
                param_values_list = param_values.get(param.name, [])
                if param_values_list and param.name in step.get("target", ""):
                    gen_step = step.copy()
                    gen_step["target"] = f"{{{param.name}}}"
                    generalized_step_params[param.name] = {
                        "name": param.name,
                        "type": param.type,
                        "required": param.required,
                        "default": None,  # No default for template parameters
                        "description": param.description,
                        "constraints": param.constraints,
                    }
                    generalized_steps.append(gen_step)
                    replaced = True
                    break
                # Also check if the parameter value appears in the target
                for val in param_values_list:
                    if (isinstance(val, str) and len(val) >= 3
                            and val in step.get("target", "")):
                        gen_step = step.copy()
                        gen_step["target"] = step["target"].replace(
                            val, f"{{{param.name}}}")
                        generalized_step_params[param.name] = {
                            "name": param.name,
                            "type": param.type,
                            "required": param.required,
                            "default": None,  # No default for template parameters
                            "description": param.description,
                            "constraints": param.constraints,
                        }
                        generalized_steps.append(gen_step)
                        replaced = True
                        break
            else:
                generalized_steps.append(step)
            
            # Update the step's parameters dict with generalized parameter info
            if generalized_step_params and "parameters" in gen_step:
                existing_params = gen_step.get("parameters", {})
                if isinstance(existing_params, dict):
                    for p_name, p_info in generalized_step_params.items():
                        existing_params[p_name] = p_info
                gen_step["parameters"] = existing_params
        
        # Calculate confidence based on number of executions and consistency
        confidence = min(0.9, 0.5 + (len(executions) * 0.1))
        
        return GeneralizationResult(
            generalized_parameters=generalized_params,
            generalized_steps=generalized_steps,
            confidence=confidence,
            matched_executions=len(executions)
        )


class ProcedureLearner:
    """Manages the complete procedure learning lifecycle."""
    
    def __init__(
        self,
        procedure_store: Optional[ProcedureStore] = None,
        min_successes_for_promotion: int = 2,
        min_confidence_for_promotion: float = 0.7,
        max_candidates: int = 100,
        enable_learning: bool = False,
    ):
        self.procedure_store = procedure_store or get_procedure_store()
        self.generalizer = ProcedureGeneralizer()
        self.secret_sanitizer = SecretSanitizer()
        self.binder = ParameterBinder()
        
        self.min_successes_for_promotion = min_successes_for_promotion
        self.min_confidence_for_promotion = min_confidence_for_promotion
        self.max_candidates = max_candidates
        self.enable_learning = enable_learning
        
        # In-memory candidate buffer (flushed to store on promotion)
        self._candidates: dict[str, LearningCandidate] = {}
        self._lock = threading.Lock()
        
        # Load config
        try:
            from core.config import get_config
            cfg = get_config()
            self.enable_learning = cfg.get("orchestrator", "enable_procedure_learning", default=enable_learning)
            self.min_successes_for_promotion = cfg.get("orchestrator", "min_procedure_successes", default=min_successes_for_promotion)
            self.min_confidence_for_promotion = cfg.get("orchestrator", "min_procedure_confidence", default=min_confidence_for_promotion)
            self.enable_adaptation = cfg.get("orchestrator", "enable_procedure_adaptation", default=True)
            self.enable_composition = cfg.get("orchestrator", "enable_procedure_composition", default=True)
            self.min_adaptation_confidence = cfg.get("orchestrator", "min_adaptation_confidence", default=0.6)
            self.max_composition_depth = cfg.get("orchestrator", "max_composition_depth", default=3)
        except Exception as e:
            log.warning(f"Failed to load procedure learner config: {e}")
            # Keep constructor defaults
        
        log.info(f"ProcedureLearner initialized: enabled={self.enable_learning}, "
                f"min_successes={self.min_successes_for_promotion}, "
                f"min_confidence={self.min_confidence_for_promotion}, "
                f"adaptation_enabled={self.enable_adaptation}, "
                f"composition_enabled={self.enable_composition}")
    
    def on_task_completed(
        self,
        task_state: TaskState,
        perception_before: Optional[PerceptionSnapshot],
        perception_after: Optional[PerceptionSnapshot],
        verification_result: Optional[VerificationResult],
        orchestrator_state: Optional[OrchestratorState] = None,
    ) -> None:
        """Called when a task completes successfully (DONE state)."""
        if not self.enable_learning:
            return
        
        if not task_state or orchestrator_state != OrchestratorState.DONE:
            return
        
        if not verification_result or not verification_result.success:
            return  # Only learn from verified successes
        
        # Create learning candidate from successful trace
        self._create_candidate_from_task(task_state)
    
    def on_verification_failed(
        self,
        task_state: TaskState,
        perception: Optional[PerceptionSnapshot],
        verification_result: VerificationResult,
    ) -> None:
        """Called when verification fails - record for recovery intelligence."""
        if not self.enable_learning:
            return
        
        if not task_state:
            return
        
        # Record failure for recovery intelligence
        self._record_failure(task_state)
    
    def _create_candidate_from_task(self, task_state: TaskState) -> None:
        """Create a learning candidate from a successful task trace."""
        if not task_state.subgoals:
            return
        
        # Extract steps from completed subgoals (current_step indicates how many are done)
        steps = []
        for i, sg in enumerate(task_state.subgoals):
            if i >= task_state.current_step:
                continue
            step_data = {
                "description": sg.description,
                "intent": sg.intent,
                "action_type": sg.action_type,
                "target": sg.target_description,
                "expected": sg.expected_result,
                "verification_method": sg.verification_method.name if sg.verification_method else "CUSTOM",
            }
            # Add parameters if they exist in the subgoal
            if hasattr(sg, 'parameters') and sg.parameters:
                step_data["parameters"] = sg.parameters
            steps.append(step_data)
        
        if not steps:
            return
        
        # Extract parameters from the task
        goal_text = task_state.goal
        extractor = get_parameter_extractor()
        extraction = get_parameter_extractor().extract(
            task_state.goal,
            None  # No procedure params yet
        )
        
        # Add extracted parameters to the first step (or all steps with parameters)
        if extraction.parameters:
            if steps:
                steps[0]["parameters"] = extraction.parameters
            # Phase 6: placeholderize step fields with this run's literals
            # AT TRACE TIME. Substring matching later (generalizer) can
            # never reliably recover literals (overlaps, short values,
            # per-run variants); replacing exact known values now means
            # stored traces carry {params}, never concrete data.
            for step_data in steps:
                for pname, pval in extraction.parameters.items():
                    if not isinstance(pval, str) or not pval:
                        continue
                    # Short values ("it", "a") are skipped to avoid
                    # corrupting prose — EXCEPT pure numbers, which in a
                    # trace are the parameter values themselves ("25",
                    # "37" must become {operand1}, never persist).
                    if len(pval) < 3 and not pval.strip().replace(
                            '.', '', 1).isdigit():
                        continue
                    for field in ("target", "expected", "description",
                                  "intent"):
                        try:
                            cur = step_data.get(field) or ""
                            if pval in cur:
                                step_data[field] = cur.replace(
                                    pval, "{%s}" % pname)
                        except Exception:
                            pass
        
        # Sanitize for secrets
        sanitized_goal = self.secret_sanitizer.sanitize(task_state.goal)
        
        # Create normalized key for candidate identity
        normalized_key = _normalize_candidate_key(sanitized_goal, task_state.goal_type.value if task_state.goal_type else "UNKNOWN")
        
        # Build parameters from learned_procedure if available, otherwise from extraction
        if task_state.learned_procedure:
            learned_params = [ProcedureParameter(
                name=p.name,
                type=p.type,
                required=p.required,
                description=p.description,
                default=p.default,
                constraints=p.constraints,
            ) for p in task_state.learned_procedure.parameters]
        else:
            # Use extracted parameters from the goal
            learned_params = [ProcedureParameter(
                name=param_name,
                type="string",
                required=True,
                description="",
                default=value,
                constraints={},
            ) for param_name, value in extraction.parameters.items()]
        
        candidate = LearningCandidate(
            goal=sanitized_goal,
            goal_type=task_state.goal_type.value if task_state.goal_type else "UNKNOWN",
            context=task_state.relevant_memory.__dict__ if task_state.relevant_memory else {},
            steps=steps,
            parameters=learned_params,
            verification_results=[],
            perception_requirements=[],
            source_task_id=task_state.task_id,
        )
        
        # Store the raw execution trace for generalization
        execution_trace = {
            "goal": task_state.goal,
            "steps": steps,
            "parameters": extraction.parameters,
        }
        
        with self._lock:
            # Check if similar candidate exists
            if normalized_key in self._candidates:
                existing = self._candidates[normalized_key]
                existing.success_count += 1
                # Track execution trace for generalization
                if not hasattr(existing, 'execution_traces'):
                    existing.execution_traces = []
                existing.execution_traces.append(execution_trace)
                # Update steps if new ones
                if len(candidate.steps) > len(existing.steps):
                    existing.steps = candidate.steps
                # Check if ready for promotion
                if existing.success_count >= self.min_successes_for_promotion:
                    self._promote_candidate(existing)
            else:
                # Initialize execution traces list
                candidate.execution_traces = [execution_trace]
                self._candidates[normalized_key] = candidate
                
                # Check if ready for promotion
                if candidate.success_count >= self.min_successes_for_promotion:
                    self._promote_candidate(candidate)
    
    def _record_failure(self, task_state: TaskState) -> None:
        """Record failure information for recovery intelligence."""
        if not task_state.failure_reason:
            return
        
        # Store failure info in task state for recovery intelligence
        if not hasattr(task_state, 'failure_history'):
            task_state.failure_history = []
        
        task_state.failure_history.append({
            "timestamp": datetime.now().isoformat(),
            "reason": task_state.failure_reason,
            "step": task_state.current_step,
            "action": task_state.last_action.action_type if task_state.last_action else None,
})
     
    def _promote_candidate(self, candidate: LearningCandidate) -> None:
        """Promote a candidate to a verified procedure."""
        try:
            # Generalize if we have multiple executions with varying parameters
            if hasattr(candidate, 'execution_traces') and len(candidate.execution_traces) >= self.min_successes_for_promotion:
                # Use the generalizer with the candidate's own execution traces
                generalizer = ProcedureGeneralizer()
                # Create a temporary procedure from the candidate for generalization
                temp_procedure = Procedure(
                    goal=candidate.goal,
                    goal_type=candidate.goal_type,
                    context=candidate.context,
                    steps=candidate.steps,
                    parameters=candidate.parameters,
                    verification=candidate.verification_results,
                    confidence=0.7,
                )
                gen_result = self.generalizer.generalize(
                    candidate.execution_traces,
                    temp_procedure
                )
                # Update candidate with generalized version
                candidate.steps = gen_result.generalized_steps
                candidate.parameters = gen_result.generalized_parameters
            
            proc_candidate = candidate.to_candidate()
            procedure = self.procedure_store.promote_candidate(proc_candidate)
            
            log.info(f"Promoted procedure: {candidate.goal} (success_count={candidate.success_count})")
            
        except Exception as e:
            log.exception(f"Failed to promote candidate: {e}")
    
    def _check_for_adaptation(self, task_state: TaskState, 
                               perception_before: Optional[PerceptionSnapshot],
                               perception_after: Optional[PerceptionSnapshot],
                               verification_result: Optional[VerificationResult]) -> None:
        """Check if a procedure needs adaptation based on context changes or failures."""
        if not self.enable_adaptation:
            return
        
        if not verification_result or verification_result.success:
            return  # Only adapt on failures
        
        # Try to find a procedure that matches the failed task
        from .procedure_store import get_procedure_store
        store = get_procedure_store()
        
        # Look for procedures matching the failed task
        procs = self.procedure_store.find_by_goal(task_state.goal, min_confidence=0.5)
        if not procs:
            return
        
        # Check if we have an adaptation store
        from .procedure_adaptation import get_adaptation_store, ProcedureAdaptationManager
        adaptation_store = get_adaptation_store()
        adaptation_manager = ProcedureAdaptationManager(
            procedure_store=self.procedure_store,
            adaptation_store=get_adaptation_store()
        )
        
        # Detect adaptation need
        adaptation = adaptation_manager.detect_adaptation_need(
            base_procedure_id=procs[0].id if procs else 0,
            perception_before=None,  # Could be enhanced with before perception
            perception_after=None,
            failed_verification=None
        )
        
        if adaptation:
            # Store the adaptation for future use
            from .procedure_adaptation import get_adaptation_store
            adaptation_store = get_adaptation_store()
            adaptation_store.save(adaptation)
            log.info(f"Created adaptation candidate for: {task_state.goal}")
        """Get statistics about current candidates."""
        with self._lock:
            return {
                "total_candidates": len(self._candidates),
                "ready_for_promotion": sum(1 for c in self._candidates.values() 
                                           if c.success_count >= self.min_successes_for_promotion),
                "by_goal_type": {
                    gt: sum(1 for c in self._candidates.values() if c.goal_type == gt)
                    for gt in set(c.goal_type for c in self._candidates.values())
                },
            }
    
    def cleanup_old_candidates(self, max_age_days: int = 30) -> int:
        """Remove old candidates that haven't been promoted."""
        cutoff = datetime.now() - timedelta(days=max_age_days)
        removed = 0
        with self._lock:
            to_remove = []
            for key, candidate in self._candidates.items():
                created = datetime.fromisoformat(candidate.created_at)
                if created < cutoff and candidate.success_count < self.min_successes_for_promotion:
                    to_remove.append(key)
            for key in to_remove:
                del self._candidates[key]
                removed += 1
        return removed


# Integration helper
def get_procedure_learner(
    procedure_store: Optional[ProcedureStore] = None,
    min_successes_for_promotion: int = 2,
    min_confidence_for_promotion: float = 0.7,
    max_candidates: int = 100,
    enable_learning: bool = False,
) -> ProcedureLearner:
    """Factory to create a ProcedureLearner instance."""
    return ProcedureLearner(
        procedure_store=procedure_store,
        min_successes_for_promotion=min_successes_for_promotion,
        min_confidence_for_promotion=min_confidence_for_promotion,
        max_candidates=max_candidates,
        enable_learning=enable_learning,
    )