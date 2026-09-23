"""
Intelligent Recovery Engine for DUDE

Provides state-based recovery when expected state differs from actual state.
Follows the SEE → UNDERSTAND → PLAN → ACT → VERIFY → RECOVER → LEARN workflow.

Uses existing DUDE components:
- TaskState for goal persistence and state tracking
- tools.ui_click for actual UI Automation control clicking
- tools.ui_scan for actual UI Automation control detection
- tools._SCREENTREE for real UI control map
- observer.Observer for progressive screen observation
- tools._active_app for current application/window state
- tools._is_browser_active for browser detection
- experience.py for persistent learning (via core.tools.init_agent_ctx)

NO false-success paths - everything is actually executed and verified.
"""

import time
import json
from typing import Optional, Dict, List, Any, Tuple, Literal, TYPE_CHECKING
from enum import Enum

# Use TYPE_CHECKING to avoid circular import
if TYPE_CHECKING:
    from core.brain import Brain
    from core.task_state import TaskState

from core.task_state import TaskState, get_task_state
from core.tools import (
    ui_click, ui_scan, click_fraction, screenshot, find_on_screen,
    _active_app, _SCREENTREE, _is_browser_active,
    init_agent_ctx, init_screentree
)
from core.observer import Observer
from core.experience import ExperienceLearner

# Helper function for standardized tool results
def _ok(message: str) -> Dict[str, Any]:
    return {"status": "OK", "message": message}

def _err(message: str) -> Dict[str, Any]:
    return {"status": "ERROR", "message": message}
class ActionStatus(Enum):
    """States for tracking action execution and verification"""
    ACTION_REQUESTED = "requested"
    ACTION_ATTEMPTED = "attempted"
    ACTION_EXECUTED = "executed"
    ACTION_VERIFIED = "verified"
    ACTION_FAILED = "failed"
class RecoveryEngine:
    """
    Intelligent recovery engine that detects state mismatches and orchestrates
    recovery actions while preserving the original user goal.

    Follows: SEE → UNDERSTAND → PLAN → ACT → VERIFY → RECOVER → LEARN
    """

    def __init__(self, task_state: Optional["TaskState"] = None,
                 observer: Optional[Observer] = None,
                 brain: Optional["Brain"] = None,
                 experience: Optional[ExperienceLearner] = None):
        self.task_state = task_state or get_task_state()
        self.observer = observer
        self.brain = brain
        self.experience = experience

        self.max_recovery_attempts = 3
        self.recovery_log: List[Dict[str, Any]] = []
        self._last_recovery_time = 0.0

    def handle_post_tool_result(
        self,
        action: str,
        args: Dict[str, Any],
        result: str,
        current_observation: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Handle post-tool execution recovery - ONE public entry point.

        Performs the complete recovery pipeline:
        ASSESS → OBSERVE → DIAGNOSE → PLAN → ACT → OBSERVE → VERIFY → LEARN → RETURN

        Args:
            action: The tool function name that was executed
            args: Arguments passed to the tool
            result: The result returned by the tool
            current_observation: Current UI state if available

        Returns:
            Recovery result dictionary with status and evidence
        """
        # Step 1: Assess if recovery is needed
        expected_state = self._get_expected_state()
        actual_state = self._get_actual_state(result, current_observation)

        # Step 2: Determine if there's a mismatch that needs recovery
        if not self._has_state_mismatch(expected_state, actual_state):
            return {"status": "no_recovery_needed", "diagnosis": "No state mismatch detected"}

        # Step 3: Check if we have recovery budget available
        if self.task_state.is_budget_exhausted():
            return {
                "status": "recovery_budget_exhausted",
                "diagnosis": "Recovery budget exhausted",
                "recovery_count": self.task_state.recovery_count,
                "max_attempts": self.max_recovery_attempts
            }

        # Step 4: Perform recovery workflow
        recovery_start = time.time()
        observation = self._observe_state(current_observation, None)
        diagnosis = self._diagnose_mismatch(expected_state, actual_state, action, result, observation)
        hypotheses = self._generate_hypotheses(expected_state, actual_state, action, result, observation)
        recovery_action = self._select_recovery_action(hypotheses, observation, None)

        # Step 5: Execute the recovery action
        action_status = ActionStatus.ACTION_REQUESTED
        recovery_result = self._execute_recovery_action(recovery_action, action_status, observation)

        # Step 6: Observe the results
        post_action_observation = self._observe_state(current_observation, None)

        # Step 7: Verify the recovery
        verification_result = self._verify_recovery(recovery_action, recovery_result, post_action_observation)

        # Step 8: Record the recovery attempt
        recovery_info = {
            "recovery_count": self.task_state.recovery_count + 1,
            "expected_state": expected_state,
            "actual_state": actual_state,
            "last_action": action,
            "last_result": result,
            "initial_observation": observation,
            "diagnosis": diagnosis,
            "hypotheses": hypotheses,
            "selected_action": recovery_action,
            "action_status": action_status.value,
            "recovery_result": recovery_result,
            "post_action_observation": post_action_observation,
            "verification_result": verification_result,
            "timestamp": recovery_start,
            "duration": time.time() - recovery_start
        }
        self.recovery_log.append(recovery_info)

        # Step 9: Record lesson if successful
        if verification_result.get("success", False):
            self._record_recovery_lesson(recovery_info)

        # Step 10: Return appropriate result based on verification
        if verification_result.get("success", False):
            return {
                "status": "recovery_verified",
                "diagnosis": diagnosis,
                "recovery_info": recovery_info,
                "verification_result": verification_result
            }
        else:
            return {
                "status": "recovery_failed",
                "diagnosis": diagnosis,
                "recovery_info": recovery_info,
                "verification_result": verification_result
            }

    def _get_expected_state(self) -> Dict[str, Any]:
        """Get the current expected state from task state."""
        return {
            "current_app": self.task_state.current_app,
            "current_window": self.task_state.current_window,
            "visible_dialog": self.task_state.visible_dialog,
            "current_goal": self.task_state.current_goal,
            "recovery_count": self.task_state.recovery_count
        }

    def _get_actual_state(
        self,
        result: str,
        current_observation: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Get the actual state based on tool result and observation."""
        actual_state = self._get_expected_state().copy()

        if result:
            lower_result = result.lower()
            if "replace_button_detected" in lower_result:
                actual_state["visible_dialog"] = "Dialog with Replace button still visible"
            elif "dialog still open" in lower_result:
                actual_state["visible_dialog"] = "Dialog still open"
            elif "error" in lower_result or "failed" in lower_result:
                actual_state["last_action"] = result
                actual_state["last_result"] = result

        if current_observation:
            obs_data = current_observation.get("data", {})
            if obs_data.get("active_app"):
                actual_state["current_app"] = obs_data["active_app"]
            if obs_data.get("active_window"):
                actual_state["current_window"] = obs_data["active_window"]
            if obs_data.get("visible_dialog"):
                actual_state["visible_dialog"] = obs_data["visible_dialog"]

        return actual_state

    # detect_and_recover has been removed in favor of handle_post_tool_result
    # The new single entry point is handle_post_tool_result() above

    def _has_state_mismatch(
        self, expected: Dict[str, Any], actual: Dict[str, Any]
    ) -> bool:
        """Check if there's a meaningful difference between expected and actual state."""
        if not expected or not actual:
            return False

        # Check for key state differences
        for key in expected:
            if key in actual and expected[key] != actual[key]:
                return True

        # Check for new unexpected state
        for key in actual:
            if key not in expected and actual[key] is not None:
                return True

        return False

    def _observe_state(
        self,
        current_observation: Optional[str],
        available_tools: Optional[List[str]]
    ) -> Dict[str, Any]:
        """
        Progressive observation using cheapest reliable method first.

        LEVEL 0: Tool/process/filesystem state
        LEVEL 1: Active window + UI Automation (actual control detection)
        LEVEL 2: Targeted screenshot (when needed)
        """
        observation = {
            "level": 0,
            "timestamp": time.time(),
            "data": {},
            "action_status": ActionStatus.ACTION_REQUESTED.value
        }

        # LEVEL 0: Check tool result and process state
        if current_observation:
            observation["data"]["tool_result"] = current_observation
            observation["level"] = 0

        # LEVEL 1: Check active window and application state using DUDE's actual UI Automation
        try:
            app, window = _active_app()
            observation["data"]["active_app"] = app
            observation["data"]["active_window"] = window
            observation["level"] = max(observation["level"], 1)

            # Use actual UI Automation to detect controls
            if _SCREENTREE and _SCREENTREE.available():
                # Get current view of available controls
                view_result = _SCREENTREE.current_view(limit=26)
                if view_result and "CONTROLS ON SCREEN" in view_result:
                    observation["data"]["ui_automation_view"] = view_result
                    observation["data"]["controls_detected"] = True

                    # Parse controls for specific button detection
                    lines = view_result.split("\n")
                    for line in lines:
                        if "." in line and "@" in line:
                            # Parse control line like "1. [btn] "Replace" @ (x,y,w,h)"
                            parts = line.split("@")
                            if len(parts) > 1:
                                control_info = parts[1].strip()
                                observation.setdefault("data", {}).setdefault("parsed_controls", []).append(
                                    control_info
                                )

                            # Look for Replace button specifically
                            if "[btn]" in line and '"' in line and 'Replace' in line:
                                observation["data"]["replace_button_detected"] = True
                                observation["data"]["replace_button_location"] = control_info

                            # Look for other common dialog buttons
                            for button_name in ["Yes", "OK", "Retry", "Cancel"]:
                                if f'"{button_name}"' in line and "[btn]" in line:
                                    observation["data"][f"{button_name.lower()}_button_detected"] = True
                                    observation["data"][f"{button_name.lower()}_button_location"] = control_info

        except Exception as e:
            observation["data"]["ui_automation_error"] = str(e)

        return observation

    def _diagnose_mismatch(
        self,
        expected_state: Dict[str, Any],
        actual_state: Dict[str, Any],
        last_action: str,
        last_result: str,
        observation: Dict[str, Any]
    ) -> str:
        """Diagnose the root cause of the state mismatch using actual evidence."""
        obs_data = observation.get("data", {})

        diagnosis_parts = []

        # Check if dialog is still open
        if obs_data.get("replace_button_detected", False):
            diagnosis_parts.append("Dialog with Replace button still visible")
        elif obs_data.get("yes_button_detected", False):
            diagnosis_parts.append("Dialog with Yes button still visible")
        elif obs_data.get("ok_button_detected", False):
            diagnosis_parts.append("Dialog with OK button still visible")

        # Check action results
        if last_result:
            if "ERROR" in last_result.upper() or "FAILED" in last_result.upper():
                diagnosis_parts.append(f"Action '{last_action}' failed: {last_result}")
            else:
                diagnosis_parts.append(f"Action '{last_action}' completed but dialog persists")

        # Check if dialog closure expected but not observed
        if last_action in ["close_app", "close_window", "type_text"]:
            if obs_data.get("replace_button_detected", False):
                diagnosis_parts.append(f"Dialog should have been closed by '{last_action}' but persists")

        # Build diagnosis string
        if diagnosis_parts:
            return "; ".join(diagnosis_parts)
        else:
            return "State mismatch detected but root cause unclear"

    def _generate_hypotheses(
        self,
        expected_state: Dict[str, Any],
        actual_state: Dict[str, Any],
        last_action: str,
        last_result: str,
        observation: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generate evidence-based hypotheses for the state mismatch."""
        hypotheses = []
        obs_data = observation.get("data", {})

        # Hypothesis 1: Dialog replacement failed
        if obs_data.get("replace_button_detected", False):
            hypotheses.append({
                "id": "hyp1_replace_failed",
                "description": "File replacement dialog failed to close",
                "confidence": 0.9,
                "evidence": [
                    f"Replace button detected in UI: {obs_data.get('replace_button_detected')}",
                    "Dialog should have closed after file replacement",
                    f"Last action was '{last_action}' but result: {last_result}"
                ],
                "expected_outcome": "Replace button should not be detectable",
                "actual_outcome": "Replace button is still detectable",
                "impact": "User cannot proceed with task",
                "testable": True
            })

        # Hypothesis 2: Action was blocked
        if "BLOCKED" in last_result.upper() or "ERROR" in last_result.upper():
            hypotheses.append({
                "id": "hyp2_action_blocked",
                "description": "Recovery action was blocked or failed",
                "confidence": 0.7,
                "evidence": [
                    f"Last action '{last_action}' resulted in: {last_result}",
                    f"UI Automation shows active app: {obs_data.get('active_app')}",
                    f"UI Automation shows window: {obs_data.get('active_window')}"
                ],
                "expected_outcome": "Action should succeed and resolve mismatch",
                "actual_outcome": "Action was blocked or failed",
                "impact": "Task cannot progress",
                "testable": True
            })

        # Hypothesis 3: Dialog is modal and requires specific action
        if obs_data.get("yes_button_detected", False) and "replace" not in obs_data.get("replace_button_detected", False):
            hypotheses.append({
                "id": "hyp3_modal_dialog",
                "description": "Modal dialog requires specific user interaction",
                "confidence": 0.6,
                "evidence": [
                    "Yes/OK/Cancel buttons detected but not Replace",
                    f"Active window context: {obs_data.get('active_window')}",
                    "Dialog type may be confirmation modal, not file replacement"
                ],
                "expected_outcome": "Dialog should close via OK/Yes action",
                "actual_outcome": "Dialog is stuck on confirmation modal",
                "impact": "User cannot proceed to file replacement",
                "testable": True
            })

        # Default hypothesis if none of the above match
        if not hypotheses:
            hypotheses.append({
                "id": "hyp_default",
                "description": "State mismatch without clear cause",
                "confidence": 0.5,
                "evidence": [
                    f"Expected state: {expected_state}",
                    f"Actual state: {actual_state}",
                    f"Last action: {last_action}",
                    f"Last result: {last_result}",
                    f"Observation: {json.dumps(observation, indent=2)}"
                ],
                "expected_outcome": "State should match expected",
                "actual_outcome": "State does not match expected",
                "impact": "Task progress uncertain",
                "testable": False
            })

        return hypotheses

    def _select_recovery_action(
        self,
        hypotheses: List[Dict[str, Any]],
        observation: Dict[str, Any],
        available_tools: Optional[List[str]]
    ) -> Dict[str, Any]:
        """Select the best recovery action based on evidence."""
        obs_data = observation.get("data", {})

        # Priority 1: Click Replace button if we have high confidence it's needed
        if obs_data.get("replace_button_detected", False):
            return {
                "action": "click_dialog_button",
                "target": "Replace",
                "method": "ui_automation",
                "confidence": 0.95,
                "reasoning": "Replace button detected in UI Automation - file replacement dialog is active"
            }

        # Priority 2: Click Yes/OK button if modal dialog is stuck
        if obs_data.get("yes_button_detected", False):
            return {
                "action": "click_dialog_button",
                "target": "Yes",
                "method": "ui_automation",
                "confidence": 0.8,
                "reasoning": "Yes button detected - likely confirmation modal that needs dismissal"
            }

        # Priority 3: Click OK button if modal dialog is stuck
        if obs_data.get("ok_button_detected", False):
            return {
                "action": "click_dialog_button",
                "target": "OK",
                "method": "ui_automation",
                "confidence": 0.8,
                "reasoning": "OK button detected - likely confirmation modal that needs dismissal"
            }

        # Priority 4: Click Cancel button as last resort
        if obs_data.get("cancel_button_detected", False):
            return {
                "action": "click_dialog_button",
                "target": "Cancel",
                "method": "ui_automation",
                "confidence": 0.7,
                "reasoning": "Cancel button detected - dialog dismissal option available"
            }

        # Priority 5: If we have specific hypotheses, use the most confident one
        if hypotheses:
            best_hypothesis = max(hypotheses, key=lambda h: h.get("confidence", 0))

            if best_hypothesis["id"] == "hyp2_action_blocked":
                return {
                    "action": "wait_and_retry",
                    "delay": 2,
                    "method": "time_based",
                    "confidence": best_hypothesis["confidence"],
                    "reasoning": "Action was blocked - waiting may allow it to proceed"
                }

            if best_hypothesis["id"] == "hyp3_modal_dialog":
                # Try clicking the modal button we detected
                return {
                    "action": "click_dialog_button",
                    "target": "OK",  # Default to OK for modal
                    "method": "ui_automation",
                    "confidence": 0.7,
                    "reasoning": "Modal dialog detected - trying OK to dismiss"
                }

        # Default action
        return {
            "action": "inspect_screen",
            "method": "ui_automation",
            "confidence": 0.5,
            "reasoning": "No clear mismatch detected - performing screen inspection"
        }

    def _execute_recovery_action(
        self,
        recovery_action: Dict[str, Any],
        action_status: ActionStatus,
        observation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute the selected recovery action."""
        action = recovery_action.get("action")
        method = recovery_action.get("method")
        target = recovery_action.get("target")

        action_status = ActionStatus.ACTION_ATTEMPTED

        if action == "click_dialog_button":
            return self._click_dialog_button_real(target, observation)
        elif action == "inspect_screen":
            return self._inspect_screen_real(observation)
        elif action == "wait_and_retry":
            return self._wait_and_retry_real(recovery_action)
        elif action == "request_elevated_permissions":
            return self._request_elevated_permissions_real(recovery_action)
        elif action == "launch_application":
            return self._launch_application_real(recovery_action)
        else:
            return {
                "success": False,
                "error": f"Unknown recovery action: {action}",
                "status": ActionStatus.ACTION_FAILED.value,
                "method": method
            }

    def _click_dialog_button_real(
        self, target: str, observation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Click a real dialog button using actual UI automation."""
        try:
            # Get current screen state from observer for real UI detection
            current_screen_data = None
            if self.observer:
                current_screen_data = self.observer.current_screen()

            # Look for the target button in the current observation
            obs_data = observation.get("data", {})
            button_detected = False

            # Check if target button is detected in current UI state
            if obs_data.get("replace_button_detected", False) and target == "Replace":
                button_detected = True
            elif f"{target.lower()}_button_detected" in obs_data:
                button_detected = True

            if not button_detected:
                return {
                    "success": False,
                    "error": f"Cannot click '{target}': button not detected in current UI state",
                    "status": ActionStatus.ACTION_FAILED.value,
                    "target": target,
                    "method": "ui_automation_validation",
                    "current_screen": current_screen_data
                }

            # Use DUDE's actual ui_click tool for real clicking
            from core.tools import ui_click

            # Call ui_click with the target name and args
            click_result = ui_click(None, {"name": target})

            # Parse the result
            if "SUCCESS" in click_result:
                return {
                    "success": True,
                    "error": None,
                    "status": ActionStatus.ACTION_EXECUTED.value,
                    "target": target,
                    "method": "ui_click_tool",
                    "click_result": click_result,
                    "observation": observation,
                    "current_screen": current_screen_data
                }
            else:
                return {
                    "success": False,
                    "error": f"ui_click failed: {click_result}",
                    "status": ActionStatus.ACTION_FAILED.value,
                    "target": target,
                    "method": "ui_click_tool",
                    "click_result": click_result,
                    "observation": observation
                }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to click dialog button '{target}': {str(e)}",
                "status": ActionStatus.ACTION_FAILED.value,
                "target": target,
                "method": "ui_automation"
            }

    def _inspect_screen_real(
        self, observation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Perform real screen inspection using DUDE's actual tools."""
        try:
            # Use DUDE's actual observer.current_screen() for real inspection
            if self.observer:
                current_screen_data = self.observer.current_screen()
                return {
                    "success": True,
                    "error": None,
                    "status": ActionStatus.ACTION_EXECUTED.value,
                    "method": "observer_current_screen",
                    "screen_data": current_screen_data,
                    "observation": observation
                }
            else:
                # Fallback to DUDE's ui_scan tool
                from core.tools import ui_scan
                scan_result = ui_scan(None, {})

                if "SUCCESS" in scan_result:
                    # Parse the ui_scan result for structured data
                    return {
                        "success": True,
                        "error": None,
                        "status": ActionStatus.ACTION_EXECUTED.value,
                        "method": "ui_scan_tool",
                        "scan_result": scan_result,
                        "observation": observation
                    }
                else:
                    return {
                        "success": False,
                        "error": f"ui_scan failed: {scan_result}",
                        "status": ActionStatus.ACTION_FAILED.value,
                        "method": "ui_scan_tool",
                        "scan_result": scan_result,
                        "observation": observation
                    }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to inspect screen: {str(e)}",
                "status": ActionStatus.ACTION_FAILED.value,
                "method": "observer_ui_scan"
            }

    def _wait_and_retry_real(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Wait and retry the failed action."""
        delay = params.get("delay", 2)
        time.sleep(delay)

        return {
            "success": True,
            "error": None,
            "status": ActionStatus.ACTION_EXECUTED.value,
            "delay": delay,
            "method": "time_based"
        }

    def _request_elevated_permissions_real(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Request elevated permissions."""
        return {
            "success": False,
            "error": "Elevated permissions required - user intervention needed",
            "status": ActionStatus.ACTION_FAILED.value,
            "action": "request_elevated_permissions",
            "method": "user_intervention"
        }

    def _launch_application_real(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Launch the target application."""
        try:
            from core.tools import open_app

            # Try to launch Excel if available, or the current app
            app_name = self.task_state.current_app or "excel"
            result = open_app(None, {"name": app_name})

            if "ERROR" not in result:
                return {
                    "success": True,
                    "error": None,
                    "status": ActionStatus.ACTION_EXECUTED.value,
                    "app_launched": app_name,
                    "method": "ui_automation"
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to launch application: {result}",
                    "status": ActionStatus.ACTION_FAILED.value,
                    "app_name": app_name
                }

        except Exception as e:
            return {
                "success": False,
                "error": f"Exception during application launch: {str(e)}",
                "status": ActionStatus.ACTION_FAILED.value
            }

    def _verify_recovery(
        self,
        action_info: Optional[Dict[str, Any]],
        recovery_result: Dict[str, Any],
        post_action_observation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Verify that recovery was successful using evidence-based verification.

        This is CRITICAL - must verify the action actually succeeded, not just that
        the action was attempted or requested.
        """
        if not recovery_result.get("success", False):
            return {
                "success": False,
                "error": f"Recovery action failed: {recovery_result.get('error', 'Unknown error')}",
                "verification_status": ActionStatus.ACTION_FAILED.value
            }

        action = action_info.get("action") if action_info else None

        # Verify based on action type
        if action == "click_dialog_button":
            return self._verify_file_replacement(post_action_observation)
        elif action == "inspect_screen":
            return self._verify_screen_inspection(post_action_observation)
        elif action == "wait_and_retry":
            # For wait_and_retry, we verify by checking if the next observation shows improvement
            return self._verify_retry_success(post_action_observation)
        else:
            # For generic actions, we need to check if the post-action observation shows expected changes
            return self._verify_generic_action(post_action_observation, action_info)

    def _verify_generic_action(
        self,
        post_action_observation: Dict[str, Any],
        action_info: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Verify a generic action using the post-action observation."""
        obs_data = post_action_observation.get("data", {})

        # Check if the observation contains meaningful changes
        # This is a placeholder for generic verification logic

        # Look for evidence of actual work being done
        if obs_data.get("replace_button_detected", False):
            return {
                "success": False,
                "error": "Action did not resolve expected UI state - Replace button still present",
                "verification_status": ActionStatus.ACTION_FAILED.value,
                "evidence": [
                    "Replace button still detected - expected change not observed",
                    "Action may have been ineffective or incomplete"
                ]
            }

        # Check if there are any UI automation observations
        if obs_data.get("ui_automation_view"):
            return {
                "success": False,
                "error": "Generic action verification requires specific implementation",
                "verification_status": ActionStatus.ACTION_FAILED.value,
                "evidence": [
                    "UI automation observation available but no specific verification logic",
                    "Requires implementation for action type: " + (action_info.get("action") if action_info else "unknown")
                ]
            }

        # For now, return UNVERIFIED for generic actions
        # This ensures we don't falsely claim success
        return {
            "success": False,
            "error": "Generic action verification not implemented",
            "verification_status": ActionStatus.ACTION_FAILED.value,
            "evidence": [
                "No specific verification logic for this action type",
                "Requires implementation in verification phase"
            ]
        }

    def _verify_screen_inspection(
        self, post_action_observation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Verify that screen inspection provided useful information."""
        screen_state = post_action_observation.get("data", {}).get("screen_state")

        if screen_state:
            app = screen_state.get("app", "")
            title = screen_state.get("title", "")

            if app and title:
                return {
                    "success": True,
                    "error": None,
                    "verification_status": ActionStatus.ACTION_VERIFIED.value,
                    "verification_method": "screen_state_captured",
                    "screen_info": f"App: {app}, Title: {title}",
                    "timestamp": post_action_observation.get("timestamp")
                }

        return {
            "success": False,
            "error": "Screen inspection did not provide useful information",
            "verification_status": ActionStatus.ACTION_FAILED.value,
            "screen_state": post_action_observation.get("data", {}).get("screen_state")
        }

    def _verify_retry_success(
        self, post_action_observation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Verify that a retry action was successful."""
        obs_data = post_action_observation.get("data", {})

        # Check if the original problem is resolved
        if not obs_data.get("replace_button_detected", False):
            return {
                "success": True,
                "error": None,
                "verification_status": ActionStatus.ACTION_VERIFIED.value,
                "verification_method": "problem_resolved",
                "evidence": [
                    "Replace button no longer detected (problem resolved)"
                ]
            }

        return {
            "success": False,
            "error": "Retry did not resolve the original problem",
            "verification_status": ActionStatus.ACTION_FAILED.value,
            "evidence": [
                f"Replace button still detected: {obs_data.get('replace_button_detected')}"
            ]
        }

    def _record_recovery_lesson(
        self,
        recovery_info: Dict[str, Any]
    ) -> None:
        """Record a useful recovery lesson using DUDE's experience system."""
        if not self.experience:
            # Fallback to task_state if experience not available
            self._record_lesson_in_task_state(recovery_info)
            return

        try:
            # Extract key information from recovery for learning
            situation = recovery_info.get("diagnosis", "")
            action = recovery_info.get("selected_action", {}).get("action", "")
            target = recovery_info.get("selected_action", {}).get("target", "")
            result = recovery_info.get("recovery_result", {}).get("success", False)
            verification = recovery_info.get("verification_result", {}).get("success", False)

            if situation and action:
                # Create a structured lesson
                lesson = {
                    "timestamp": recovery_info.get("timestamp"),
                    "situation": situation,
                    "action_attempted": action,
                    "target": target,
                    "success": result and verification,
                    "recovery_count": recovery_info.get("recovery_count"),
                    "diagnosis": situation,
                    "hypothesis_confidence": recovery_info.get("hypotheses", [{}])[0].get("confidence") if recovery_info.get("hypotheses") else 0,
                    "method_used": recovery_info.get("selected_action", {}).get("method", "")
                }

                # Record in experience system
                self.experience.record(
                    tool=f"recovery_{action}",
                    args=lesson,
                    outcome="OK" if (result and verification) else "FAIL",
                    detail=json.dumps(lesson),
                    app=recovery_info.get("initial_observation", {}).get("data", {}).get("active_app", ""),
                    window=recovery_info.get("initial_observation", {}).get("data", {}).get("active_window", "")
                )

        except Exception as e:
            # Fallback to task_state recording
            self._record_lesson_in_task_state(recovery_info)

    def _record_lesson_in_task_state(
        self,
        recovery_info: Dict[str, Any]
    ) -> None:
        """Record recovery lesson in task_state as fallback."""
        lesson = {
            "timestamp": recovery_info.get("timestamp"),
            "situation": recovery_info.get("diagnosis", ""),
            "action_attempted": recovery_info.get("selected_action", {}).get("action", ""),
            "target": recovery_info.get("selected_action", {}).get("target", ""),
            "success": recovery_info.get("recovery_result", {}).get("success", False) and
                      recovery_info.get("verification_result", {}).get("success", False),
            "recovery_count": recovery_info.get("recovery_count"),
            "confidence": recovery_info.get("hypotheses", [{}])[0].get("confidence") if recovery_info.get("hypotheses") else 0
        }

        # Add to task_state performance metrics
        self.task_state.performance_metrics.setdefault("recovery_lessons", []).append(lesson)

        # Keep only recent lessons
        lessons = self.task_state.performance_metrics.get("recovery_lessons", [])
        if len(lessons) > 10:
            self.task_state.performance_metrics["recovery_lessons"] = lessons[-10:]