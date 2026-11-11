"""Parameter Binding for DUDE Orchestrator.

Binds extracted parameters to procedure steps to create executable plans.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .state import Procedure, ProcedureParameter, SubGoal, Plan, TaskState, VerificationMethod, RiskLevel
from .parameter_extractor import get_parameter_extractor, ExtractionResult


@dataclass
class BindingResult:
    """Result of parameter binding."""
    plan: Optional["Plan"] = None
    bound_parameters: dict[str, Any] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    success: bool = True


class ParameterBinder:
    """Binds extracted parameters to procedure steps to create executable plans."""
    
    def __init__(self, min_confidence: float = 0.6):
        self.min_confidence = min_confidence
        self._extractor = get_parameter_extractor()
    
    def bind(
        self,
        procedure: "Procedure",
        user_intent: str,
        task_state: "TaskState",
        perception: Any = None,
    ) -> BindingResult:
        """Bind parameters from user intent to a procedure."""
        result = BindingResult()
        
        # Extract parameters from user intent
        extraction = get_parameter_extractor().extract(user_intent, procedure.parameters)
        
        # Check for missing required parameters
        for param in procedure.parameters:
            if param.required and param.name not in extraction.parameters:
                if param.default is not None:
                    extraction.parameters[param.name] = param.default
                else:
                    result.missing_required.append(param.name)
                    result.errors.append(f"Missing required parameter: {param.name}")
        
        if result.missing_required:
            result.success = False
            result.errors.append(f"Missing required parameters: {', '.join(result.missing_required)}")
            return result
        
        # Validate extracted values
        for param_name, value in extraction.parameters.items():
            # Find parameter definition
            param_def = next((p for p in procedure.parameters if p.name == param_name), None)
            if param_def:
                validated = self._validate_value(extraction.parameters[param_name], param_def)
                if validated is None:
                    result.errors.append(f"Invalid value for parameter '{param_name}': {extraction.parameters[param_name]}")
                    result.success = False
                else:
                    extraction.parameters[param_name] = validated
        
        if result.errors:
            result.success = False
            return result
        
        # Bind parameters to procedure steps
        bound_steps = self._bind_steps(procedure.steps, extraction.parameters)
        
        # Convert bound steps to SubGoals
        subgoals = []
        for i, step in enumerate(bound_steps):
            # Determine verification method from step or use default
            verification_method = step.get("verification_method")
            if isinstance(verification_method, str):
                try:
                    from .state import VerificationMethod
                    verification_method = VerificationMethod[verification_method]
                except (KeyError, AttributeError):
                    verification_method = VerificationMethod.CUSTOM
            
            subgoal = SubGoal(
                description=step.get("description", ""),
                intent=step.get("intent", ""),
                action_type=step.get("action_type", "execute"),
                target_description=step.get("target", ""),
                expected_result=step.get("expected", ""),
                verification_method=step.get("verification_method"),
                risk_level=step.get("risk_level"),
                order=i,
            )
            # We'll add the bound parameters to the target spec
            # This will be handled by the task engine when creating actions
            
        # Create the plan
        plan = Plan(
            task_id="",  # Will be set by TaskEngine
            objective=extraction.parameters.get("intent", ""),
            steps=subgoals,
            confidence=0.8,
            source="procedure",
            required_perception=1,
            verification=[],
            risk_level=RiskLevel.LOW,
        )
        
        result.plan = Plan(
            task_id="",
            objective=extraction.parameters.get("intent", ""),
            steps=[],
            confidence=0.8,
            source="procedure",
            required_perception=1,
            verification=[],
            risk_level=RiskLevel.LOW,
        )
        result.bound_parameters = extraction.parameters
        result.success = True
        
        return result
    
    def _bind_steps(self, steps: list[dict], parameters: dict[str, Any]) -> list[dict]:
        """Bind parameters to procedure steps."""
        bound_steps = []
        for step in steps:
            bound_step = copy.deepcopy(step)
            
            # Bind parameters in string fields
            for key, value in bound_step.items():
                if isinstance(value, str):
                    bound_step[key] = self._substitute(value, bound_step.get("parameters", {}))
            
            # Also substitute in nested parameters field
            if "parameters" in bound_step and isinstance(bound_step["parameters"], dict):
                for param_name, param_value in bound_step["parameters"].items():
                    if isinstance(param_value, str):
                        bound_step["parameters"][param_name] = self._substitute(param_value, bound_step["parameters"])
            
            bound_steps.append(bound_step)
        
        return bound_steps
    
    def _substitute(self, text: str, params: dict[str, Any]) -> str:
        """Substitute parameter placeholders in text.
        
        Placeholders use the format {param_name} or {{param_name}}.
        """
        if not isinstance(text, str):
            return text
        
        # First pass: {{param}} (double braces)
        result = re.sub(r'\{\{(\w+)\}\}', lambda m: str(params.get(m.group(1), m.group(0))), text)
        # Second pass: {param} (single braces)
        result = re.sub(r'\{(\w+)\}', lambda m: str(params.get(m.group(1), m.group(0))), result)
        return result
    
    def _validate_value(self, value: Any, param_def: Any) -> Any:
        """Validate a value against a parameter definition."""
        if param_def is None:
            return value
        
        # Type validation
        if param_def.type == "integer":
            try:
                return int(value)
            except (ValueError, TypeError):
                return None
        elif param_def.type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lower = value.lower()
                if lower in ("true", "yes", "1", "on"):
                    return True
                elif lower in ("false", "no", "0", "off"):
                    return False
                return None
            return None
        elif param_def.type == "directory":
            if not isinstance(value, str):
                return None
            sanitized = re.sub(r'[<>:"|?*]', '', value).strip()
            if not sanitized:
                return None
            return sanitized
        elif param_def.type in ("path", "filename"):
            if not isinstance(value, str):
                return None
            sanitized = value.strip()
            if '..' in sanitized or sanitized.startswith('/'):
                return None
            return sanitized
        elif param_def.type == "app_name":
            if not isinstance(value, str):
                return None
            return value.strip()
        elif param_def.type == "string":
            if not isinstance(value, str):
                return str(value)
            return value.strip()
        
        return value


def bind_procedure(
    procedure: "Procedure",
    user_intent: str,
    task_state: Any = None,
    perception: Any = None,
) -> "Plan":
    """Convenience function to bind a procedure and return a plan."""
    from .state import Plan, TaskState, SubGoal, VerificationMethod, RiskLevel
    
    # Simple binding for now - create a basic plan
    plan = Plan(
        task_id="",
        objective=user_intent,
        steps=[],
        confidence=0.8,
        source="procedure",
        required_perception=1,
        verification=[],
        risk_level=RiskLevel.LOW,
    )
    return plan