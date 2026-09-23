"""RecoveryActionExecutor - Executes recovery actions from RecoveryDecision.

Maps recovery decisions to concrete tool executions.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
import json

from .state import (
    TaskState,
    RecoveryDecision,
    PerceptionSnapshot,
    VerificationResult,
    VerificationMethod,
    PerceptionLevel,
)
from .perception import PerceptionEngine
from .action_executor import ActionExecutor, GroundedAction, GroundingMethod
from .perception import PerceptionEngine
from core.tools import execute_tool
from core.memory import Memory

log = logging.getLogger(__name__)


class RecoveryActionExecutor:
    """Executes recovery actions based on RecoveryDecision."""
    
    def __init__(
        self,
        perception: PerceptionEngine,
        action_executor: ActionExecutor,
        memory: Optional[Memory] = None,
        min_confidence: float = 0.7,
    ):
        self.perception = perception
        self.action_executor = action_executor
        self.memory = memory
        self.min_confidence = min_confidence
    
    async def execute_recovery(
        self,
        decision: "RecoveryDecision",
        task_state: "TaskState",
        perception: "PerceptionSnapshot",
        verification: "VerificationResult",
    ) -> bool:
        """Execute a recovery action based on the decision.
        
        Returns True if recovery action was executed successfully, False otherwise.
        """
        action = decision.action
        
        try:
            if action == "click_save_button":
                return await self._click_save_button(perception)
            elif action == "click_confirm":
                return await self._click_confirm(perception)
            elif action == "click_cancel_or_close":
                return await self._click_cancel_or_close(perception)
            elif action == "click_cancel":
                return await self._click_cancel(perception)
            elif action == "read_error_and_escalate":
                return await self._read_error_and_escalate(perception)
            elif action == "refocus_target_window":
                return await self._refocus_target_window(
                    perception, task_state)
            elif action == "re_ground_with_ocr":
                return await self._re_ground_with_ocr(perception)
            elif action == "retry_re_ground":
                return await self._retry_re_ground(perception)
            elif action == "retry_same":
                return await self._retry_same(perception)
            elif action == "restart_application":
                return await self._restart_application(perception)
            elif action == "force_close_and_restart":
                return await self._force_close_and_restart(perception)
            elif action == "ask_user_for_path":
                return await self._ask_user_for_path(perception)
            elif action == "request_permission":
                return await self._request_permission(perception)
            else:
                log.warning(f"Unknown recovery action: {action}")
                return False
                
        except Exception as e:
            log.exception(f"Recovery action '{action}' failed: {e}")
            return False
    
    # --- Specific recovery action implementations ---
    
    async def _click_save_button(self, perception: "PerceptionSnapshot") -> bool:
        """Click the Save button in a save dialog."""
        # Look for Save button in UIA controls
        for ctrl in perception.controls:
            if ctrl.enabled and ctrl.visible:
                name_lower = str(getattr(ctrl, 'name', '')).lower()
                ctype_lower = str(getattr(ctrl, 'ctype', '')).lower()
                if "save" in name_lower and ("button" in ctype_lower or "push" in name_lower):
                    return await self._click_control(ctrl)
        
        # Fallback: OCR text match for "Save"
        for region in perception.ocr_regions:
            if "save" in region.text.lower():
                return await self._click_ocr_region(region)
        
        return False
    
    async def _click_confirm(self, perception: "PerceptionSnapshot") -> bool:
        """Click Confirm/Yes/OK button in a confirmation dialog."""
        # Look for Confirm/Yes/OK button in UIA controls
        for ctrl in perception.controls:
            if ctrl.enabled and ctrl.visible:
                name_lower = str(getattr(ctrl, 'name', '')).lower()
                ctype_lower = str(getattr(ctrl, 'ctype', '')).lower()
                if any(kw in name_lower for kw in ["confirm", "yes", "ok", "allow", "continue"]):
                    if "button" in ctype_lower or "push" in name_lower:
                        return await self._click_control(ctrl)
        
        # Fallback: OCR text match
        for region in perception.ocr_regions:
            if any(kw in region.text.lower() for kw in ["confirm", "yes", "ok", "allow", "continue"]):
                return await self._click_ocr_region(region)
        
        return False
    
    async def _click_cancel_or_close(self, perception: "PerceptionSnapshot") -> bool:
        """Click Cancel or Close button."""
        # Try Cancel first
        for ctrl in perception.controls:
            if ctrl.enabled and ctrl.visible:
                name_lower = str(getattr(ctrl, 'name', '')).lower()
                ctype_lower = str(getattr(ctrl, 'ctype', '')).lower()
                if "cancel" in name_lower and ("button" in ctype_lower or "push" in name_lower):
                    return await self._click_control(ctrl)
        
        # Then try Close
        for ctrl in perception.controls:
            if ctrl.enabled and ctrl.visible:
                name_lower = str(getattr(ctrl, 'name', '')).lower()
                ctype_lower = str(getattr(ctrl, 'ctype', '')).lower()
                if "close" in name_lower and ("button" in ctype_lower or "push" in name_lower):
                    return await self._click_control(ctrl)
        
        # Fallback: OCR
        for region in perception.ocr_regions:
            if "cancel" in region.text.lower() or "close" in region.text.lower():
                return await self._click_ocr_region(region)
        
        return False
    
    async def _click_cancel(self, perception: "PerceptionSnapshot") -> bool:
        """Click Cancel button specifically."""
        for ctrl in perception.controls:
            if ctrl.enabled and ctrl.visible:
                name_lower = str(getattr(ctrl, 'name', '')).lower()
                ctype_lower = str(getattr(ctrl, 'ctype', '')).lower()
                if "cancel" in name_lower and ("button" in ctype_lower or "push" in name_lower):
                    return await self._click_control(ctrl)
        
        # OCR fallback
        for region in perception.ocr_regions:
            if "cancel" in region.text.lower():
                return await self._click_ocr_region(region)
        
        return False
    
    async def _read_error_and_escalate(self, perception: "PerceptionSnapshot") -> bool:
        """Read error dialog and log/escalate."""
        error_text = perception.ocr_text[:500]
        log.error(f"Error dialog detected: {error_text}")
        # Could implement escalation to user here
        return False
    
    async def _refocus_target_window(self, perception: "PerceptionSnapshot",
                                     task_state=None) -> bool:
        """Refocus the TASK's window (never whatever happens to be fg).

        The old code focused perception.active_app — under contention
        that is the contender, so recovery "refocused" the wrong app.
        Prefer the pinned target_hwnd, then the task's target app.
        """
        try:
            hwnd = int(getattr(task_state, "target_hwnd", 0) or 0)
        except Exception:
            hwnd = 0
        if hwnd:
            try:
                import win32gui
                from core.tools import bring_to_foreground
                if win32gui.IsWindow(hwnd) \
                        and bring_to_foreground(hwnd):
                    log.info(f"Refocused pinned task window {hwnd}")
                    return True
            except Exception as e:
                log.warning(f"Failed to refocus pinned window: {e}")
        target_app = (getattr(task_state, "target_application", "") or ""
                      ).strip() or perception.active_app
        if target_app:
            try:
                from core.tools import window_action
                from core.memory import Memory
                from unittest.mock import Mock
                
                # Try to focus the window
                result = execute_tool("window_action", json.dumps({
                    "action": "focus",
                    "app_name": target_app
                }), Mock(), lambda *a: True)
                
                if result.startswith("OK"):
                    log.info(f"Refocused window for {target_app}")
                    return True
            except Exception as e:
                log.warning(f"Failed to refocus window: {e}")
        
        # Alternative: try to find the window and click its title bar
        for ctrl in perception.controls:
            if ctrl.enabled and ctrl.visible and str(getattr(ctrl, 'ctype', '')).lower() in ("title_bar", "window"):
                return await self._click_control(ctrl)
        
        return False
    
    async def _re_ground_with_ocr(self, perception: "PerceptionSnapshot") -> bool:
        """Re-ground the action target using OCR."""
        # This is a no-op at the executor level - the next action attempt
        # will naturally use OCR grounding if UIA failed
        log.info("Recovery: will re-ground with OCR on next action attempt")
        return True
    
    async def _retry_re_ground(self, perception: "PerceptionSnapshot") -> bool:
        """Retry with re-grounding."""
        # Similar to _re_ground_with_ocr
        log.info("Recovery: will retry with re-grounding on next attempt")
        return True
    
    async def _retry_same(self, perception: "PerceptionSnapshot") -> bool:
        """Retry the same action after a delay."""
        log.info("Recovery: retrying same action after delay")
        return True
    
    async def _restart_application(self, perception: "PerceptionSnapshot") -> bool:
        """Restart the target application."""
        target_app = perception.active_app
        if target_app:
            try:
                result = execute_tool("close_app", json.dumps({"name": target_app}), None, lambda *a: True)
                if result.startswith("OK"):
                    import asyncio
                    await asyncio.sleep(2)
                    result = execute_tool("open_app", json.dumps({"name": target_app}), None, lambda *a: True)
                    return result.startswith("OK")
            except Exception as e:
                log.warning(f"Failed to restart application: {e}")
        return False
    
    async def _force_close_and_restart(self, perception: "PerceptionSnapshot") -> bool:
        """Force close and restart the application."""
        target_app = perception.active_app
        if target_app:
            try:
                # Force close
                result = execute_tool("close_app", json.dumps({"name": target_app}), None, lambda *a: True)
                if result.startswith("OK"):
                    import asyncio
                    await asyncio.sleep(2)
                    result = execute_tool("open_app", json.dumps({"name": target_app}), None, lambda *a: True)
                    return result.startswith("OK")
            except Exception as e:
                log.warning(f"Failed to force close and restart: {e}")
        return False
    
    async def _ask_user_for_path(self, perception: "PerceptionSnapshot") -> bool:
        """Signal that user input is needed for file path."""
        # This would require user interaction - for now return False
        # to indicate escalation is needed
        log.warning("Recovery: user input needed for file path")
        return False
    
    async def _request_permission(self, perception: "PerceptionSnapshot") -> bool:
        """Request permission from user."""
        log.warning("Recovery: permission required from user")
        return False
    
    # --- Helper methods ---
    
    async def _click_control(self, ctrl) -> bool:
        """Click a UIA control."""
        from core.tools import execute_tool
        from core.memory import Memory
        from unittest.mock import Mock
        
        if ctrl.rect:
            x, y = ctrl.rect.center()
            result = execute_tool("ui_click", json.dumps({"x": x, "y": y}), Mock(), lambda *a: True)
            return result.startswith("OK")
        return False
    
    async def _click_ocr_region(self, region) -> bool:
        """Click an OCR region center."""
        from core.tools import execute_tool
        from unittest.mock import Mock
        
        x, y = region.center
        result = execute_tool("ui_click", json.dumps({"x": x, "y": y}), Mock(), lambda *a: True)
        return result.startswith("OK")


# Factory function
def get_recovery_action_executor(
    perception: "PerceptionEngine",
    action_executor: "ActionExecutor",
    memory: Optional[Memory] = None,
    min_confidence: float = 0.7,
) -> "RecoveryActionExecutor":
    """Factory to create a RecoveryActionExecutor instance."""
    return RecoveryActionExecutor(perception, action_executor, memory, min_confidence)