"""Core state dataclasses for the DUDE Orchestrator.

These define the authoritative task runtime state. No new database;
all fields map to existing memory tables or transient in-memory structures.
"""
from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Optional


class TaskType(Enum):
    """High-level task classification."""
    CREATE = "create"
    MODIFY = "modify"
    QUERY = "query"
    AUTOMATE = "automate"
    LEARN = "learn"
    NAVIGATE = "navigate"
    UNKNOWN = "unknown"


class OrchestratorState(Enum):
    """Top-level orchestrator state machine."""
    IDLE = "idle"
    LISTENING = "listening"
    UNDERSTANDING = "understanding"
    OBSERVING = "observing"
    PLANNING = "planning"
    ACTING = "acting"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    DONE = "done"
    FAILED = "failed"


class VoiceState(Enum):
    """Voice output state for coordination with barge-in."""
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    ACTING = "acting"
    VERIFYING = "verifying"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    RECOVERING = "recovering"
    DONE = "done"
    FAILED = "failed"


class PerceptionLevel(IntEnum):
    """Perception hierarchy levels (1 = fastest/cheapest, 5 = slowest/expensive)."""
    LEVEL_1_APP_WINDOW = 1      # Active app/window identity
    LEVEL_2_UIA_TREE = 2        # UI Automation controls
    LEVEL_3_OCR = 3             # Text extraction (tesseract)
    LEVEL_4_TARGETED_CV = 4     # Screenshot region + CV template/feature match
    LEVEL_5_VISION_MODEL = 5    # Vision model (budget-gated)


class InterruptRelation(Enum):
    """How an interrupting utterance relates to the current task."""
    MODIFICATION = "modification"
    CANCELLATION = "cancellation"
    NEW_TASK = "new_task"
    CLARIFICATION = "clarification"
    UNRELATED = "unrelated"


class UnexpectedState(Enum):
    """Classified unexpected UI states during execution."""
    MODAL_DIALOG = "modal_dialog"
    APPLICATION_CRASH = "app_crash"
    APPLICATION_HANG = "app_hang"
    PERMISSION_DENIED = "permission_denied"
    FILE_NOT_FOUND = "file_not_found"
    NETWORK_ERROR = "network_error"
    UI_CHANGED = "ui_changed"
    FOCUS_LOST = "focus_lost"
    UNKNOWN = "unknown"


class VerificationMethod(Enum):
    """How to verify an action's outcome."""
    UIA_STATE_CHANGE = "uia_state_change"
    OCR_TEXT_APPEARED = "ocr_text_appeared"
    OCR_TEXT_DISAPPEARED = "ocr_text_disappeared"
    FILE_EXISTS = "file_exists"
    WINDOW_APPEARED = "window_appeared"
    WINDOW_DISAPPEARED = "window_disappeared"
    PROCESS_STATE = "process_state"
    CLIPBOARD_CONTENT = "clipboard_content"
    SCREEN_DELTA = "screen_delta"
    FOCUSED_CONTROL = "focused_control"
    TEXT_READ = "text_read"
    CUSTOM = "custom"


def parse_verification_method(value) -> "VerificationMethod":
    """Restore a VerificationMethod from a stored name/value.

    Learned traces persist the method; retrieval must honor it —
    defaulting everything to CUSTOM would make retrieved procedures
    unverifiable. Unknown values fail closed to CUSTOM.
    """
    if isinstance(value, VerificationMethod):
        return value
    if isinstance(value, str):
        try:
            return VerificationMethod[value.strip().upper()]
        except KeyError:
            pass
        try:
            return VerificationMethod(value.strip().lower())
        except ValueError:
            pass
    return VerificationMethod.CUSTOM


class GroundingMethod(Enum):
    """How an action target was grounded against the screen."""
    UIA = "uia"
    UIA_ROLE = "uia_role"
    OCR_TEXT = "ocr_text"
    CV_TEMPLATE = "cv_template"
    RELATIVE_COORDS = "relative_coords"
    ABSOLUTE_COORDS = "absolute_coords"
    KEYBOARD_DIRECT = "keyboard_direct"
    KEYBOARD_FOCUSED = "keyboard_focused"


class RiskLevel(Enum):
    """Security risk classification for actions."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PermissionState(Enum):
    """Permission status for an action."""
    GRANTED = "granted"
    PENDING = "pending"
    DENIED = "denied"
    NOT_REQUIRED = "not_required"


@dataclass
class RecoveryDecision:
    """Decision made by recovery engine."""
    should_retry: bool
    reason: str
    action: str
    new_grounding_method: Optional[str] = None
    modified_action: Optional[Any] = None
    wait_seconds: float = 0.0


@dataclass
class Rect:
    """Screen rectangle in pixels."""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h


@dataclass
class ControlInfo:
    """UI Automation control information."""
    ctype: str = ""
    name: str = ""
    automation_id: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    enabled: bool = True
    visible: bool = True
    focused: bool = False
    role: str = ""

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.w, self.h)


@dataclass
class OCRRegion:
    """OCR text region with bounding box."""
    text: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    confidence: float = 0.0

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.w, self.h)

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)


@dataclass
class ScreenDelta:
    """What changed between two perceptions."""
    app_changed: bool = False
    window_changed: bool = False
    uia_tree_changed: bool = False
    ocr_changed: bool = False
    screenshot_diff: float = 0.0
    change_detected: bool = False
    changed_regions: list[Rect] = field(default_factory=list)
    new_controls: list[ControlInfo] = field(default_factory=list)
    removed_controls: list[ControlInfo] = field(default_factory=list)
    timestamp: float = 0.0


@dataclass
class SubGoal:
    """A decomposed step toward the main goal."""
    description: str = ""
    intent: str = ""
    action_type: str = ""
    target_description: str = ""
    expected_result: str = ""
    completed: bool = False
    verification_method: VerificationMethod = VerificationMethod.CUSTOM
    risk_level: RiskLevel = RiskLevel.LOW
    order: int = 0


@dataclass
class StepResult:
    """Result of executing one subgoal step."""
    subgoal: SubGoal
    action: Optional["Action"] = None
    success: bool = False
    actual_result: str = ""
    verification: Optional["VerificationResult"] = None
    error: str = ""
    recovery_attempts: int = 0
    duration_seconds: float = 0.0


@dataclass
class TargetSpec:
    """Specification of what an action acts upon."""
    control_id: Optional[str] = None
    control_name: Optional[str] = None
    control_role: Optional[str] = None
    text_match: Optional[str] = None
    template_path: Optional[str] = None
    coordinates: Optional[tuple[int, int]] = None
    relative_coords: Optional[tuple[float, float]] = None
    window_title_regex: Optional[str] = None
    process_name: Optional[str] = None
    # Phase 5: pin the action to one exact window (HWND) chosen at plan
    # time, and/or request a fresh window instead of reusing an open one.
    hwnd: Optional[int] = None
    fresh_window: bool = False


@dataclass
class ExpectedResult:
    """What should be true after a successful action."""
    uia_property: Optional[str] = None
    uia_value: Any = None
    text_expected: Optional[str] = None
    file_path: Optional[str] = None
    window_title: Optional[str] = None
    process_name: Optional[str] = None
    custom_check: Optional[str] = None


@dataclass
class ActualResult:
    """What actually happened after an action."""
    uia_property: Optional[str] = None
    uia_value: Any = None
    text_found: Optional[str] = None
    file_exists: Optional[bool] = None
    window_title: Optional[str] = None
    process_name: Optional[str] = None
    custom_result: Any = None


@dataclass
class VerificationResult:
    """Result of verifying an action's outcome."""
    success: bool = False
    method: VerificationMethod = VerificationMethod.CUSTOM
    evidence: str = ""
    confidence: float = 0.0
    expected: Optional[ExpectedResult] = None
    actual: Optional[ActualResult] = None
    before_screenshot: Optional[bytes] = None
    after_screenshot: Optional[bytes] = None


@dataclass
class Action:
    """Structured action object for the orchestrator."""
    task_id: str
    step_id: int
    intent: str
    action_type: str
    target: TargetSpec
    target_description: str
    grounding_method: GroundingMethod = GroundingMethod.UIA
    coordinates: Optional[tuple[int, int]] = None
    expected_result: ExpectedResult = field(default_factory=ExpectedResult)
    verification_method: VerificationMethod = VerificationMethod.CUSTOM
    confidence: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    retry_count: int = 0
    max_retries: int = 3
    timeout_seconds: float = 30.0
    pre_actions: list["Action"] = field(default_factory=list)


@dataclass
class MemoryBundle:
    """Relevant memory retrieved for a task."""
    facts: list[dict] = field(default_factory=list)
    procedures: list[dict] = field(default_factory=list)
    experience_matches: list[dict] = field(default_factory=list)
    recent_messages: list[dict] = field(default_factory=list)
    work_context: Optional[dict] = None


@dataclass
class ProcedureParameter:
    """A parameter definition for a procedure step."""
    name: str
    type: str = "string"  # string, path, app_name, filename, directory, integer, boolean
    required: bool = True
    description: str = ""
    default: Any = None


@dataclass
class Plan:
    """Structured execution plan for a task."""
    task_id: str
    objective: str
    steps: list[SubGoal] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "unknown"  # skill, procedure, local, model
    required_perception: int = 1  # PerceptionLevel
    verification: list[VerificationMethod] = field(default_factory=list)
    recovery_hints: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    
    def to_actions(self, task_state: TaskState, perception: PerceptionSnapshot) -> list[Action]:
        """Convert plan steps to executable Actions."""
        from .state import TaskType
        
        actions = []
        for i, step in enumerate(self.steps):
            # Determine grounding method based on step
            grounding = GroundingMethod.UIA
            if step.verification_method == VerificationMethod.OCR_TEXT_APPEARED:
                grounding = GroundingMethod.OCR_TEXT
            elif step.verification_method == VerificationMethod.SCREEN_DELTA:
                grounding = GroundingMethod.RELATIVE_COORDS
            
            action = Action(
                task_id=task_state.task_id,
                step_id=i,
                intent=step.intent,
                action_type=step.action_type or "execute",
                target=TargetSpec(text_match=step.target_description),
                target_description=step.target_description,
                grounding_method=grounding,
                expected_result=ExpectedResult(
                    text_expected=step.expected_result if step.expected_result else None,
                ),
                verification_method=step.verification_method,
                confidence=self.confidence,
                risk_level=step.risk_level,
            )
            actions.append(action)
        return actions


@dataclass
class ProcedureParameter:
    """A parameter definition for a procedure step."""
    name: str
    type: str = "string"  # string, path, app_name, filename, directory, integer, boolean
    required: bool = True
    description: str = ""
    default: Any = None
    constraints: dict = field(default_factory=dict)  # e.g., {"pattern": "^[a-zA-Z0-9_-]+$"}


@dataclass
class Procedure:
    """A learned procedure (verified workflow)."""
    goal: str = ""
    goal_type: str = "UNKNOWN"
    context: dict = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)  # Each step can have "parameters" field
    parameters: list[ProcedureParameter] = field(default_factory=list)  # Global procedure parameters
    expected_states: list[dict] = field(default_factory=list)
    verification: list[dict] = field(default_factory=list)
    exceptions: dict = field(default_factory=dict)
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0.0
    source: str = "observed"
    version: int = 1
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    # Versioning
    version_history: list[dict] = field(default_factory=list)  # Previous versions
    active: bool = True  # Whether this version is active
    change_log: list[str] = field(default_factory=list)  # Human-readable change descriptions
    # Adaptation tracking
    adaptation_parent_id: Optional[int] = None  # ID of the procedure this was adapted from
    adaptation_reason: str = ""  # Why this adaptation was created
    adaptation_count: int = 0  # Number of times this procedure has been adapted
    # Database ID (assigned after save)
    id: Optional[int] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "goal": self.goal,
            "goal_type": self.goal_type,
            "context": self.context,
            "preconditions": self.preconditions,
            "steps": self.steps,
            "parameters": [p.__dict__ if hasattr(p, '__dict__') else p for p in self.parameters],
            "expected_states": self.expected_states,
            "verification": self.verification,
            "exceptions": self.exceptions,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "confidence": self.confidence,
            "source": self.source,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version_history": self.version_history,
            "active": self.active,
            "change_log": self.change_log,
            "adaptation_parent_id": self.adaptation_parent_id,
            "adaptation_reason": self.adaptation_reason,
            "adaptation_count": self.adaptation_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Procedure":
        """Create Procedure from dictionary."""
        procedure = cls(
            goal=data.get("goal", ""),
            goal_type=data.get("goal_type", "UNKNOWN"),
            context=data.get("context", {}),
            preconditions=data.get("preconditions", []),
            steps=data.get("steps", []),
            expected_states=data.get("expected_states", []),
            verification=data.get("verification", []),
            exceptions=data.get("exceptions", {}),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            confidence=data.get("confidence", 0.0),
            source=data.get("source", "observed"),
            version=data.get("version", 1),
            created_at=data.get("created_at", datetime.datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.datetime.now().isoformat()),
            version_history=data.get("version_history", []),
            active=data.get("active", True),
            change_log=data.get("change_log", []),
            adaptation_parent_id=data.get("adaptation_parent_id"),
            adaptation_reason=data.get("adaptation_reason", ""),
            adaptation_count=data.get("adaptation_count", 0),
        )
        # Load parameters
        if "parameters" in data and isinstance(data["parameters"], list):
            for p_data in data.get("parameters", []):
                if isinstance(p_data, dict):
                    param = ProcedureParameter(**p_data)
                    procedure.parameters.append(param)
                else:
                    procedure.parameters.append(p_data)
        return procedure


@dataclass
class ProcedureAdaptation:
    """An adaptation of a procedure for a changed context."""
    base_procedure_id: int  # ID of the original procedure
    adapted_procedure_id: Optional[int] = None  # ID of the adapted procedure (after promotion)
    trigger: str = ""  # What triggered the adaptation (e.g., "ui_changed", "parameter_changed")
    context_diff: dict = field(default_factory=dict)  # What changed in context
    parameter_changes: dict = field(default_factory=dict)  # Parameter value changes
    step_modifications: list[dict] = field(default_factory=list)  # Modified steps
    reason: str = ""  # Human-readable reason for adaptation
    status: str = "candidate"  # candidate, verified, rejected
    confidence: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    verified_at: Optional[str] = None
    success_count: int = 0
    failure_count: int = 0


@dataclass
class ProcedureComposition:
    """A composition of multiple procedures into a larger workflow."""
    name: str
    description: str = ""
    sub_procedures: list[int] = field(default_factory=list)  # IDs of sub-procedures
    execution_order: list[int] = field(default_factory=list)  # Order of execution
    parameters: list[ProcedureParameter] = field(default_factory=list)  # Shared parameters
    verification: list[dict] = field(default_factory=list)  # Composition-level verification
    confidence: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    active: bool = True
    version: int = 1
    created_by: str = "composer"  # Who/what created this composition
    id: Optional[int] = None


@dataclass
class TaskState:
    """Authoritative task runtime state. Single instance per active task."""
    # Identity
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    updated_at: datetime.datetime = field(default_factory=datetime.datetime.now)

    # Goal
    goal: str = ""
    goal_type: TaskType = TaskType.UNKNOWN
    subgoals: list[SubGoal] = field(default_factory=list)

    # Progress
    current_step: int = 0
    completed_steps: list[StepResult] = field(default_factory=list)
    pending_steps: list[SubGoal] = field(default_factory=list)

    # Context
    current_application: str = "unknown"
    current_window: dict = field(default_factory=dict)
    user_interrupt: Optional[str] = None

    # Perception
    last_observation: Optional["PerceptionSnapshot"] = None
    screen_delta: Optional[ScreenDelta] = None

    # Action
    last_action: Optional[Action] = None
    last_action_result: Optional[StepResult] = None
    expected_result: Optional[ExpectedResult] = None
    actual_result: Optional[ActualResult] = None
    # Where the last grounded interaction landed on screen. Keyboard
    # actions have no target of their own; their visible effects appear
    # around this point, so verification may crop there.
    last_grounded_coordinates: Optional[tuple[int, int]] = None

    # Verification
    verification_method: VerificationMethod = VerificationMethod.CUSTOM
    verification_result: Optional[VerificationResult] = None
    confidence: float = 0.0

    # Recovery
    failure_reason: Optional[str] = None
    recovery_attempts: int = 0
    recovery_history: list[dict] = field(default_factory=list)

    # Phase 5: execution ownership (which windows/files this task owns
    # vs merely borrows; borrowed ones are never closed or modified
    # destructively) and cancellation.
    execution_mode: str = "foreground"  # foreground | background
    target_application: str = ""
    target_hwnd: Optional[int] = None
    owned_hwnds: set = field(default_factory=set)
    owned_temp_files: list = field(default_factory=list)
    cancel_requested: bool = False
    cancelled: bool = False
    # Phase 5: window reuse decisions, newest last
    # ({phase, app, decision, hwnd, reason}).
    window_decisions: list[dict] = field(default_factory=list)
    # Phase 5: efficiency counters (filled by TaskEngine as it runs).
    metrics: dict = field(default_factory=dict)

    # Memory
    relevant_memory: MemoryBundle = field(default_factory=MemoryBundle)
    learned_procedure: Optional[Procedure] = None

    # Permissions
    permission_state: PermissionState = PermissionState.NOT_REQUIRED

    # Phase 5: tasks that need an isolated window (not a reused one) set
    # this; the planner turns it into fresh_window targets on open steps.
    prefer_fresh_windows: bool = False

    # Voice
    voice_state: VoiceState = VoiceState.IDLE
    pending_speech: Optional[str] = None
    interrupted_speech: Optional[str] = None

    def current_subgoal(self) -> Optional[SubGoal]:
        if 0 <= self.current_step < len(self.subgoals):
            return self.subgoals[self.current_step]
        return None

    def advance_step(self) -> bool:
        self.current_step += 1
        self.updated_at = datetime.datetime.now()
        return self.current_step < len(self.subgoals)

    def is_complete(self) -> bool:
        return self.current_step >= len(self.subgoals) and len(self.subgoals) > 0

    def can_retry(self) -> bool:
        return self.recovery_attempts < 3

    def mark_recovery(self, reason: str, action: str):
        self.recovery_attempts += 1
        self.recovery_history.append({
            "attempt": self.recovery_attempts,
            "reason": reason,
            "action": action,
            "timestamp": datetime.datetime.now().isoformat()
        })
        self.updated_at = datetime.datetime.now()


@dataclass
class PerceptionSnapshot:
    """Complete perception state at a moment in time."""
    active_app: str = "unknown"
    active_window: dict = field(default_factory=dict)
    window_bounds: Optional[Rect] = None
    uia_tree: Optional[list[ControlInfo]] = None
    controls: list[ControlInfo] = field(default_factory=list)
    focused_control: Optional[ControlInfo] = None
    ocr_text: str = ""
    ocr_regions: list[OCRRegion] = field(default_factory=list)
    screenshot: Optional[bytes] = None
    screenshot_age: float = 0.0
    change_detected: bool = False
    changed_regions: list[Rect] = field(default_factory=list)
    vision_analysis: Optional[str] = None
    vision_age: float = 0.0
    captured_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    capture_method: "PerceptionLevel" = None
    age_seconds: float = 0.0

    def is_fresh(self, max_age: float = 5.0) -> bool:
        return self.age_seconds <= max_age