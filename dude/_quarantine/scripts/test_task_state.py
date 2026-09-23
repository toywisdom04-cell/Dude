#!/usr/bin/env python3
"""
Unit tests for TaskState implementation.

These tests validate the Core State Management system for DUDE
without requiring a full Python environment or DUDE runtime.
"""

import sys
import os
from pathlib import Path

# Add the dude directory to path
sys.path.insert(0, str(Path(__file__).parent))

from core.task_state import TaskState, get_task_state, reset_task_state


def test_task_state_initialization():
    """Test TaskState initialization."""
    print("Running: test_task_state_initialization")
    state = TaskState()

    # Check initial values
    assert state.original_goal is None
    assert state.current_goal is None
    assert state.current_subtask is None
    assert state.subtasks == []
    assert state.completed_subtasks == []
    assert state.current_app is None
    assert state.current_window is None
    assert state.visible_dialog is None
    assert state.ui_controls == []
    assert state.expected_state == {}
    assert state.actual_state == {}
    assert state.last_action is None
    assert state.last_result is None
    assert state.action_history == []
    assert state.current_observation is None
    assert state.diagnosis is None
    assert state.hypotheses == []
    assert state.selected_next_action is None
    assert state.recovery_count == 0
    assert state.recovery_budget == 3
    assert state.continuation_requested is False
    assert state.is_complete() is False
    assert state.get_remaining_subtasks_count() == 0
    assert state.get_completed_subtasks_count() == 0

    print("✓ PASS")


def test_goal_persistence():
    """Test goal setting and persistence."""
    print("Running: test_goal_persistence")
    state = TaskState()

    # Set goal
    state.set_current_goal("Create Excel report")
    assert state.original_goal == "Create Excel report"
    assert state.current_goal == "Create Excel report"
    assert state.current_subtask is None

    # Update subtask
    state.update_subtask("Find source data")
    assert state.current_subtask == "Find source data"
    assert "Find source data" in state.subtasks

    print("✓ PASS")


def test_subtask_completion():
    """Test subtask completion tracking."""
    print("Running: test_subtask_completion")
    state = TaskState()
    state.set_current_goal("Test task")
    state.subtasks = ["Step 1", "Step 2", "Step 3"]

    # Complete first subtask
    result = state.complete_subtask("Step 1", "Success")
    assert result is True
    assert "Step 1" in state.completed_subtasks
    assert "Step 1" not in state.subtasks
    assert state.get_completed_subtasks_count() == 1
    assert state.get_remaining_subtasks_count() == 2

    # Try to complete non-existent subtask
    result = state.complete_subtask("Step 4", "Success")
    assert result is False
    assert state.get_completed_subtasks_count() == 1

    print("✓ PASS")


def test_application_state():
    """Test application state tracking."""
    print("Running: test_application_state")
    state = TaskState()

    # Update application state
    state.update_application_state("Excel", "Sheet1 - Budget")
    assert state.current_app == "Excel"
    assert state.current_window == "Sheet1 - Budget"

    # Update again
    state.update_application_state("PowerPoint", "Presentation")
    assert state.current_app == "PowerPoint"
    assert state.current_window == "Presentation"

    print("✓ PASS")


def test_expected_vs_actual():
    """Test expected vs actual state comparison."""
    print("Running: test_expected_vs_actual")
    state = TaskState()

    # Set expected state
    state.set_expected_state({"file_opened": True, "permissions": "read"})
    assert state.expected_state == {"file_opened": True, "permissions": "read"}

    # Set actual state
    state.set_actual_state({"file_opened": True, "permissions": "write"})
    assert state.actual_state == {"file_opened": True, "permissions": "write"}

    # Check mismatch detection
    assert state.has_mismatch() is True

    # Update actual to match expected
    state.set_actual_state({"file_opened": True, "permissions": "read"})
    assert state.has_mismatch() is False

    print("✓ PASS")


def test_recovery_system():
    """Test recovery action tracking."""
    print("Running: test_recovery_system")
    state = TaskState()

    # Check initial state
    assert state.recovery_count == 0
    assert state.has_recovery_budget() is True

    # Simulate recovery actions
    state.select_recovery_action("click_replace_button")
    assert state.selected_next_action == "click_replace_button"
    assert state.recovery_count == 1

    # Use another recovery action
    state.select_recovery_action("grant_permissions")
    assert state.selected_next_action == "grant_permissions"
    assert state.recovery_count == 2

    # Check recovery budget
    assert state.has_recovery_budget() is True

    # Exhaust recovery budget
    state.select_recovery_action("retry_operation")
    state.select_recovery_action("abort_task")
    state.select_recovery_action("try_alternative")
    assert state.recovery_count == 5
    assert state.has_recovery_budget() is False

    print("✓ PASS")


def test_task_completion():
    """ test task completion tracking."""
    print("Running: test_task_completion")
    state = TaskState()

    # Initially not complete
    assert state.is_complete() is False

    # Complete all subtasks
    state.subtasks = ["Step 1"]
    state.completed_subtasks = ["Step 1"]

    # Should be complete
    assert state.is_complete() is True
    assert state.get_completed_subtasks_count() == 1
    assert state.get_remaining_subtasks_count() == 0

    # Reset task
    state.reset_for_new_task()
    assert state.is_complete() is False
    assert state.get_completed_subtasks_count() == 0

    print("✓ PASS")


def test_performance_metrics():
    """Test performance metrics calculation."""
    print("Running: test_performance_metrics")
    state = TaskState()

    # Set up test scenario
    state.set_current_goal("Test task")
    state.subtasks = ["Step 1", "Step 2", "Step 3"]
    state.completed_subtasks = ["Step 1", "Step 2"]

    # Update performance metrics
    state.update_performance_metrics()

    # Check metrics
    assert state.performance_metrics['task_completion_percentage'] == 2/3
    assert state.performance_metrics['efficiency_score'] > 0

    print("✓ PASS")


def test_hypothesis_tracking():
    """Test hypothesis management."""
    print("Running: test_hypothesis_tracking")
    state = TaskState()

    # Add hypotheses
    state.add_hypothesis("File is locked by another process")
    state.add_hypothesis("Insufficient permissions")
    state.add_hypothesis("File path is invalid")

    assert len(state.hypotheses) == 3
    assert "File is locked by another process" in state.hypotheses
    assert "Insufficient permissions" in state.hypotheses
    assert "File path is invalid" in state.hypotheses

    print("✓ PASS")


def test_diagnosis():
    """Test diagnosis functionality."""
    print("Running: test_diagnosis")
    state = TaskState()

    # Set diagnosis
    state.diagnose_mismatch("Overwrite confirmation dialog required")
    assert state.diagnosis == "Overwrite confirmation dialog required"

    print("✓ PASS")


def test_action_history():
    """Test action history tracking."""
    print("Running: test_action_history")
    state = TaskState()

    # Record actions
    state.record_action_result("open_file", "File opened successfully", True)
    state.record_action_result("edit_content", "Content edited", True)
    state.record_action_result("save_file", "Permission denied", False)

    assert len(state.action_history) == 3
    assert state.action_history[0]['action'] == "open_file"
    assert state.action_history[0]['result'] == "File opened successfully"
    assert state.action_history[0]['success'] is True
    assert state.action_history[2]['success'] is False

    print("✓ PASS")


def test_serialization():
    """Test state serialization and deserialization."""
    print("Running: test_serialization")
    state1 = TaskState()

    # Set up test state
    state1.set_current_goal("Test goal")
    state1.current_subtask = "Step 1"
    state1.subtasks = ["Step 1", "Step 2"]
    state1.completed_subtasks = ["Step 1"]
    state1.current_app = "Excel"
    state1.current_window = "Budget"
    state1.expected_state = {"test": "expected"}
    state1.actual_state = {"test": "actual"}

    # Serialize
    state_dict = state1.to_dict()

    # Create new state and deserialize
    state2 = TaskState()
    state2.from_dict(state_dict)

    # Verify state is preserved
    assert state2.original_goal == state1.original_goal
    assert state2.current_subtask == state1.current_subtask
    assert state2.subtasks == state1.subtasks
    assert state2.completed_subtasks == state1.completed_subtasks
    assert state2.current_app == state1.current_app
    assert state2.current_window == state1.current_window
    assert state2.expected_state == state1.expected_state
    assert state2.actual_state == state1.actual_state

    print("✓ PASS")


def test_global_task_state():
    """Test global task state functions."""
    print("Running: test_global_task_state")

    # Reset global state
    reset_task_state()

    # Get global state
    state1 = get_task_state()
    assert state1 is not None

    # Get same instance
    state2 = get_task_state()
    assert state1 is state2

    # Modify through global
    state1.set_current_goal("Global test")
    assert state2.original_goal == "Global test"

    # Reset again
    reset_task_state()
    state3 = get_task_state()
    assert state3 is not None
    assert state3.original_goal is None

    print("✓ PASS")


def test_ui_state_update():
    """Test UI state update function."""
    print("Running: test_ui_state_update")

    # This would normally be called from DUDE UI automation
    # We're just testing the function exists and works
    from core.task_state import update_task_state_from_ui

    # Test the function by calling it directly
    update_task_state_from_ui("Chrome", "Google Sheets")

    # Get the task state to verify
    state = get_task_state()
    assert state.current_app == "Chrome"
    assert state.current_window == "Google Sheets"

    print("✓ PASS")


def test_tool_execution_state():
    """Test tool execution state recording."""
    print("Running: test_tool_execution_state")

    from core.task_state import record_tool_execution_state

    # Record tool execution
    record_tool_execution_state("open_app", {"name": "Chrome"}, "Success", True)

    # Get the task state to verify
    state = get_task_state()
    assert len(state.action_history) == 1
    assert state.action_history[0]['action'] == "open_app"
    assert state.action_history[0]['args'] == {"name": "Chrome"}
    assert state.action_history[0]['result'] == "Success"
    assert state.action_history[0]['success'] is True

    print("✓ PASS")


def main():
    """Run all TaskState tests."""
    print("=" * 60)
    print("TASK STATE UNIT TESTS")
    print("=" * 60)

    # Run all tests
    test_functions = [
        test_task_state_initialization,
        test_goal_persistence,
        test_subtask_completion,
        test_application_state,
        test_expected_vs_actual,
        test_recovery_system,
        test_task_completion,
        test_performance_metrics,
        test_hypothesis_tracking,
        test_diagnosis,
        test_action_history,
        test_serialization,
        test_global_task_state,
        test_ui_state_update,
        test_tool_execution_state,
    ]

    passed = 0
    failed = 0

    for test_func in test_functions:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"✗ FAIL: {e}")
            import traceback
            traceback.print_exc()

    print("=" * 60)
    print(f"TEST RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed == 0:
        print("🎉 ALL TESTS PASSED!")
        return 0
    else:
        print(f"❌ {failed} TEST(S) FAILED")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)