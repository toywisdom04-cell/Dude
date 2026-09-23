"""
Core State Management for DUDE Intelligent Agent

TaskState represents the transient state of a DUDE task/session.
It tracks goal, subtasks, application context, and state comparison
for intelligent recovery and goal persistence.

This system follows the principle of PRESERVE → EXTEND → VERIFY.
"""

import time
from typing import Optional, Dict, List, Any


class TaskState:
    """
    Core state management for DUDE intelligent agent.

    Represents the current task/session state including goals,
    application context, and expected vs actual state tracking
    for intelligent recovery and goal persistence.

    Transient task state is kept separate from permanent memory.
    """

    def __init__(self):
        # USER GOALS & TASKS
        self.original_goal: Optional[str] = None  # The initial user request
        self.current_goal: Optional[str] = None  # Current focus within multi-step task
        self.current_subtask: Optional[str] = None  # Active subtask being worked on
        self.subtasks: List[str] = []  # Ordered list of all remaining subtasks
        self.completed_subtasks: List[str] = []  # Successfully completed subtasks

        # APPLICATION & WINDOW CONTEXT
        self.current_app: Optional[str] = None  # Active application
        self.current_window: Optional[str] = None  # Active window title
        self.visible_dialog: Optional[str] = None  # Detected dialog
        self.ui_controls: List[str] = []  # Available UI controls

        # STATE COMPARISON (Expected vs Actual)
        self.expected_state: Dict[str, Any] = {}  # What should happen
        self.actual_state: Dict[str, Any] = {}  # What actually happened

        # ACTION TRACKING
        self.last_action: Optional[str] = None  # Previous operation
        self.last_result: Optional[str] = None  # Outcome of last action
        self.action_history: List[Dict[str, Any]] = []  # Complete action log

        # OBSERVATION & DIAGNOSIS
        self.current_observation: Optional[str] = None  # Current system state
        self.diagnosis: Optional[str] = None  # Current diagnosis
        self.hypotheses: List[str] = []  # Possible failure causes
        self.selected_next_action: Optional[str] = None  # Chosen recovery action

        # RECOVERY & CONTINUITY
        self.recovery_count: int = 0  # Number of recovery actions taken
        self.recovery_budget: int = 3  # Maximum allowed recovery actions
        self.continuation_requested: bool = False  # Whether to continue original goal

        # PERFORMANCE & TRACKING
        self.start_time: float = time.time()  # When current task started
        self.performance_metrics: Dict[str, Any] = {
            'expected_vs_actual_diffs': [],
            'recovery_actions': [],
            'task_completion_percentage': 0.0,
            'efficiency_score': 0.0
        }

        # Internal tracking
        self._state_changed: bool = False
        self._last_update: float = 0.0

    def set_current_goal(self, goal: str) -> None:
        """Set the current user goal."""
        self.original_goal = goal
        self.current_goal = goal
        self.current_subtask = None
        self._state_changed = True

    def create_task(self, goal: str, subtasks: List[str]) -> None:
        """Create a new task with a goal and ordered subtasks."""
        if self.has_active_task():
            raise Exception("A task is already active. Complete or cancel the current task first.")

        self.original_goal = goal
        self.current_goal = goal
        self.subtasks = subtasks[:]  # Copy the list
        self.completed_subtasks = []
        self.current_subtask = None
        self.recovery_count = 0
        self.start_time = time.time()
        self._state_changed = True
        log_task_creation(goal, subtasks)

    def complete_subtask(self, subtask: str, result: str = "") -> bool:
        """Mark a subtask as completed."""
        if subtask in self.subtasks:
            self.completed_subtasks.append(subtask)
            self.subtasks.remove(subtask)
            self.last_action = subtask
            self.last_result = result
            self.action_history.append({
                'action': subtask,
                'result': result,
                'timestamp': time.time(),
                'type': 'success'
            })
            self._state_changed = True
            return True
        return False

    def continue_with_subtask(self, subtask: str) -> bool:
        """Continue working on a specific subtask from the remaining list.

        Returns True if the subtask was successfully started.
        """
        if not self.has_active_task():
            return False

        if subtask in self.subtasks:
            return self.start_subtask(subtask)
        return False

    def record_tool_execution(self, action: str, args: Dict[str, Any], result: str, success: bool) -> None:
        """Record a tool execution in the task state."""
        self.last_action = action
        self.last_result = result
        self.action_history.append({
            'action': action,
            'args': args,
            'result': result,
            'success': success,
            'timestamp': time.time()
        })

        # Update application state if it's a relevant action
        if 'app' in args:
            self.current_app = args['app']

        self._state_changed = True

    def detect_mismatch_and_start_recovery(self, expected_state: Dict[str, Any], actual_state: Dict[str, Any]) -> bool:
        """Detect a state mismatch and start recovery process.

        Returns True if recovery was initiated, False if no mismatch or recovery budget exhausted.
        """
        self.expected_state = expected_state
        self.actual_state = actual_state

        if not self.has_mismatch():
            return False

        if not self.has_recovery_budget():
            log_recovery_budget_exhausted()
            return False

        # Start recovery process
        self.recovery_count += 1
        self._state_changed = True
        log_recovery_initiated(expected_state, actual_state)

        return True

    def check_task_continuation(self, user_input: str) -> bool:
        """Determine if user input continues the current task or starts a new one."""
        if not self.has_active_task():
            return False

        # Check if input is likely a continuation of the current goal
        if self.can_continue_goal(user_input):
            # If currently working on a subtask, continue
            if self.current_subtask:
                return True

            # No current subtask, but task is not complete - continue
            if not self.is_task_complete():
                return True

        return False

    def is_task_complete(self) -> bool:
        """Check if all subtasks are completed."""
        return len(self.subtasks) == 0 and len(self.completed_subtasks) > 0

    def has_active_task(self) -> bool:
        """Check if there's an active task with remaining subtasks."""
        return self.original_goal is not None and len(self.subtasks) > 0

    def get_remaining_subtasks_count(self) -> int:
        """Get the number of remaining subtasks."""
        return len(self.subtasks)

    def get_completed_subtasks_count(self) -> int:
        """Get the number of completed subtasks."""
        return len(self.completed_subtasks)

    def can_continue_goal(self, new_input: str) -> bool:
        """Determine if a new user input is related to the current goal.

        Returns True if the input is likely a continuation or specification
        of the current goal, False if it appears to be a new unrelated goal.
        """
        if not self.original_goal:
            return True  # No current goal, anything is acceptable

        goal_keywords = self.original_goal.lower().split()
        input_keywords = new_input.lower().split()

        if not goal_keywords:
            return True

        overlap = len(set(goal_keywords) & set(input_keywords))
        return overlap / len(goal_keywords) >= 0.3

    def reset_for_new_task(self) -> None:
        """Reset state for a new task, preserving current active task if any."""
        if self.has_active_task():
            # Current task exists, need explicit user confirmation to continue
            # Don't automatically reset
            pass
        else:
            # No active task, safe to reset
            self.original_goal = None
            self.current_goal = None
            self.current_subtask = None
            self.subtasks = []
            self.completed_subtasks = []
            self.expected_state = {}
            self.actual_state = {}
            self.last_action = None
            self.last_result = None
            self.action_history = []
            self.current_observation = None
            self.diagnosis = None
            self.hypotheses = []
            self.selected_next_action = None
            self.recovery_count = 0
            self.continuation_requested = False
            self.performance_metrics = {
                'expected_vs_actual_diffs': [],
                'recovery_actions': [],
                'task_completion_percentage': 0.0,
                'efficiency_score': 0.0
            }
            self._state_changed = False
            self._last_update = 0.0

    def has_mismatch(self) -> bool:
        """Check if there's a difference between expected and actual state."""
        return self.expected_state != self.actual_state

    def has_recovery_budget(self) -> bool:
        """Check if recovery budget allows more recovery actions."""
        return self.recovery_count < self.recovery_budget

    def update_application_state(self, app: str, window: str) -> None:
        """Update current application and window context."""
        if self.current_app != app or self.current_window != window:
            self.current_app = app
            self.current_window = window
            self._state_changed = True

    def is_budget_exhausted(self) -> bool:
        """Check if the recovery budget has been exhausted."""
        return self.recovery_count >= self.recovery_budget


def log_task_creation(goal: str, subtasks: List[str]) -> None:
    """Log task creation for debugging and auditing."""
    print(f"[TaskState] Creating new task: {goal}")
    print(f"[TaskState] Subtasks: {', '.join(subtasks)}")


def log_recovery_initiated(expected: Dict[str, Any], actual: Dict[str, Any]) -> None:
    """Log recovery process initiation."""
    print(f"[TaskState] Recovery initiated - Expected: {expected}")
    print(f"[TaskState] Recovery initiated - Actual: {actual}")


def log_recovery_budget_exhausted() -> None:
    """Log when recovery budget is exhausted."""
    print(f"[TaskState] Recovery budget exhausted - cannot attempt further recovery")

    def complete_subtask(self, subtask: str, result: str) -> bool:
        """Mark a subtask as completed.

        Returns True if the subtask was in the remaining subtasks list.
        """
        if subtask in self.subtasks:
            self.completed_subtasks.append(subtask)
            self.subtasks.remove(subtask)
            self.last_action = subtask
            self.last_result = result
            self.action_history.append({
                'action': subtask,
                'result': result,
                'timestamp': time.time(),
                'type': 'success'
            })
            self._state_changed = True
            return True
        return False

    def update_application_state(self, app: str, window: str) -> None:
        """Update current application and window context."""
        if self.current_app != app or self.current_window != window:
            self.current_app = app
            self.current_window = window
            self._state_changed = True

    def set_expected_state(self, expected: Dict[str, Any]) -> None:
        """Set the expected state for the current action."""
        self.expected_state = expected
        self._state_changed = True

    def set_actual_state(self, actual: Dict[str, Any]) -> None:
        """Set the actual state resulting from an action."""
        self.actual_state = actual
        self._state_changed = True

    def record_action_result(self, action: str, result: str, success: bool) -> None:
        """Record the result of an action."""
        self.last_action = action
        self.last_result = result
        self.action_history.append({
            'action': action,
            'result': result,
            'success': success,
            'timestamp': time.time()
        })
        self._state_changed = True

    def diagnose_mismatch(self, diagnosis: str) -> None:
        """Record a diagnosis for a state mismatch."""
        self.diagnosis = diagnosis
        self._state_changed = True

    def add_hypothesis(self, hypothesis: str) -> None:
        """Add a possible cause hypothesis."""
        self.hypotheses.append(hypothesis)
        self._state_changed = True

    def select_recovery_action(self, action: str) -> None:
        """Select the recovery action to execute."""
        self.selected_next_action = action
        self.recovery_count += 1
        self._state_changed = True

    def continue_original_goal(self) -> None:
        """Mark that recovery should continue the original goal."""
        self.continuation_requested = True
        self._state_changed = True

    def update_performance_metrics(self) -> None:
        """Update performance metrics based on current state."""
        if self.expected_state and self.actual_state:
            diffs = self._calculate_expected_vs_actual_diffs()
            self.performance_metrics['expected_vs_actual_diffs'] = diffs

        self.performance_metrics['task_completion_percentage'] = (
            len(self.completed_subtasks) / max(len(self.subtasks) + len(self.completed_subtasks), 1)
        )

        total_time = time.time() - self.start_time
        if total_time > 0:
            self.performance_metrics['efficiency_score'] = (
                len(self.completed_subtasks) / total_time
            )

    def has_mismatch(self) -> bool:
        """Check if there's a difference between expected and actual state."""
        return self.expected_state != self.actual_state

    def has_recovery_budget(self) -> bool:
        """Check if recovery budget allows more recovery actions."""
        return self.recovery_count < self.recovery_budget

    def is_complete(self) -> bool:
        """Check if all subtasks are completed."""
        return len(self.subtasks) == 0 and len(self.completed_subtasks) > 0

    def get_remaining_subtasks_count(self) -> int:
        """Get the number of remaining subtasks."""
        return len(self.subtasks)

    def get_completed_subtasks_count(self) -> int:
        """Get the number of completed subtasks."""
        return len(self.completed_subtasks)

    def _calculate_expected_vs_actual_diffs(self) -> List[Dict[str, Any]]:
        """Calculate differences between expected and actual state."""
        diffs = []
        for key in set(self.expected_state.keys()) | set(self.actual_state.keys()):
            expected_val = self.expected_state.get(key)
            actual_val = self.actual_state.get(key)
            if expected_val != actual_val:
                diffs.append({
                    'field': key,
                    'expected': expected_val,
                    'actual': actual_val,
                    'difference': f"{expected_val} → {actual_val}"
                })
        return diffs

    def reset_for_new_task(self) -> None:
        """Reset state for a new task."""
        self.original_goal = None
        self.current_goal = None
        self.current_subtask = None
        self.subtasks = []
        self.completed_subtasks = []
        self.current_app = None
        self.current_window = None
        self.visible_dialog = None
        self.ui_controls = []
        self.expected_state = {}
        self.actual_state = {}
        self.last_action = None
        self.last_result = None
        self.action_history = []
        self.current_observation = None
        self.diagnosis = None
        self.hypotheses = []
        self.selected_next_action = None
        self.recovery_count =  diverging
        self.continuation_requested = False
        self.performance_metrics = {
            'expected_vs_actual_diffs': [],
            'recovery_actions': [],
            'task_completion_percentage': 0.0,
            'efficiency_score': 0.0
        }
        self._state_changed = False
        self._last_update = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for serialization."""
        return {
            'original_goal': self.original_goal,
            'current_goal': self.current_goal,
            'current_subtask': self.current_subtask,
            'subtasks': self.subtasks,
            'completed_subtasks': self.completed_subtasks,
            'current_app': self.current_app,
            'current_window': self.current_window,
            'visible_dialog': self.visible_dialog,
            'ui_controls': self.ui_controls,
            'expected_state': self.expected_state,
            'actual_state': self.actual_state,
            'last_action': self.last_action,
            'last_result': self.last_result,
            'current_observation': self.current_observation,
            'diagnosis': self.diagnosis,
            'hypotheses': self.hypotheses,
            'selected_next_action': self.selected_next_action,
            'recovery_count': self.recovery_count,
            'continuation_requested': self.continuation_requested,
            'performance_metrics': self.performance_metrics,
            'is_complete': self.is_complete(),
            'remaining_subtasks': self.get_remaining_subtasks_count(),
            'completed_subtasks_count': self.get_completed_subtasks_count()
        }

    def from_dict(self, state_dict: Dict[str, Any]) -> None:
        """Load state from dictionary."""
        for key, value in state_dict.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self._state_changed = True

    def __str__(self) -> str:
        """String representation of current state."""
        status = "IN PROGRESS"
        if self.is_complete():
            status = "COMPLETE"
        elif self.has_mismatch() and self.has_recovery_budget():
            status = "RECOVERY NEEDED"
        elif self.has_mismatch() and not self.has_recovery_budget():
            status = "RECOVERY EXHAUSTED"

        return (f"TaskState(status={status}, "
                f"goal={self.original_goal}, "
                f"subtasks={self.get_completed_subtasks_count()}/{self.get_remaining_subtasks_count() + self.get_completed_subtasks_count()}, "
                f"recovered={self.recovery_count}, "
                f"dialog={'Yes' if self.visible_dialog else 'No'})")


# Global task state instance for DUDE
task_state: Optional[TaskState] = None


def get_task_state() -> TaskState:
    """Get or create the global task state instance."""
    global task_state
    if task_state is None:
        task_state = TaskState()
    return task_state


def reset_task_state() -> None:
    """Reset the global task state instance."""
    global task_state
    task_state = TaskState()


def update_task_state_from_ui(ui_app: str, ui_window: str) -> None:
    """Update task state from UI information.

    Called from DUDE when UI automation detects application changes.
    """
    state = get_task_state()
    state.update_application_state(ui_app, ui_window)


def record_tool_execution_state(
    action: str,
    args: Dict[str, Any],
    result: str,
    success: bool
) -> None:
    """Record a tool execution in the task state.

    Called from DUDE tools to track actions and results.
    """
    state = get_task_state()
    state.record_action_result(action, result, success)


def check_task_state_for_recovery(
    expected: Dict[str, Any],
    actual: Dict[str, Any]
) -> bool:
    """Check if there's a state mismatch that requires recovery.

    Called from DUDE to determine if recovery workflow should be triggered.
    """
    state = get_task_state()
    state.set_expected_state(expected)
    state.set_actual_state(actual)
    return state.has_mismatch() and state.has_recovery_budget()