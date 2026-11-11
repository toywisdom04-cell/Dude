"""RecoveryEngine - Bounded recovery from failed actions.

Implements structured recovery with max retries, confidence decay,
and safe escalation.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .state import (
    TaskState,
    VerificationResult,
    PerceptionSnapshot,
    UnexpectedState,
    RecoveryDecision,
)
from .perception import PerceptionEngine, PerceptionSnapshot

log = logging.getLogger(__name__)


@dataclass
class RecoveryDecision:
    """Decision made by recovery engine."""
    should_retry: bool
    reason: str
    action: str
    new_grounding_method: Optional[str] = None
    modified_action: Optional[Any] = None
    wait_seconds: float = 0.0


class RecoveryEngine:
    """Bounded recovery engine with configurable retry policy."""
    
    def __init__(
        self,
        max_retries: int = 3,
        retry_delays: tuple = (1.0, 3.0, 10.0),
        confidence_decay: float = 0.7,
        min_confidence: float = 0.3,
    ):
        self.max_retries = max_retries
        self.retry_delays = retry_delays
        self.confidence_decay = confidence_decay
        self.min_confidence = min_confidence
    
    async def decide(
        self,
        task_state: TaskState,
        perception: PerceptionSnapshot,
        verification: VerificationResult,
    ) -> RecoveryDecision:
        """Decide recovery action based on failure context."""
        
        # Check if we've exhausted retries
        if task_state.recovery_attempts >= self.max_retries:
            return RecoveryDecision(
                should_retry=False,
                reason=f"Max retries ({self.max_retries}) exceeded",
                action="escalate_to_user",
            )
        
        # Classify unexpected state
        unexpected = self._classify_unexpected(perception, verification)
        
        # Get delay for this retry attempt
        delay_idx = min(task_state.recovery_attempts, len(self.retry_delays) - 1)
        wait_seconds = self.retry_delays[delay_idx]
        
        # Decay confidence
        current_confidence = task_state.confidence * (self.confidence_decay ** task_state.recovery_attempts)
        if current_confidence < self.min_confidence:
            return RecoveryDecision(
                should_retry=False,
                reason=f"Confidence decayed below minimum ({current_confidence:.2f} < {self.min_confidence})",
                action="escalate_to_user",
                wait_seconds=wait_seconds,
            )

        # Pinned-target focus contention: when the task owns an exact
        # HWND and the foreground is something else, refocus the PINNED
        # window before retrying — never "refocus" the contender that
        # happens to be foreground. (App-name comparison alone misses
        # this: contender and task can share no names yet still collide.)
        # target_hwnd is only set after a successful open, so fall back
        # to the latest window-resolve decision HWND (the window the
        # plan just chose).
        try:
            pinned = int(getattr(task_state, "target_hwnd", 0) or 0)
        except Exception:
            pinned = 0
        if not pinned:
            try:
                for d in reversed(getattr(task_state, "window_decisions",
                                          None) or []):
                    h = (d or {}).get("hwnd") if isinstance(d, dict) else None
                    if isinstance(h, int) and not isinstance(h, bool) and h:
                        pinned = h
                        break
            except Exception:
                pass
        try:
            fg_hwnd = int((perception.active_window or {}).get("hwnd", 0)
                          or 0)
        except Exception:
            fg_hwnd = 0
        if pinned and fg_hwnd and pinned != fg_hwnd:
            return RecoveryDecision(
                should_retry=True,
                reason=(f"Focus contention: task owns window {pinned}, "
                        f"foreground is {fg_hwnd}; refocusing task window"),
                action="refocus_target_window",
                wait_seconds=wait_seconds,
            )
        
        # Strategy based on unexpected state
        if unexpected == UnexpectedState.MODAL_DIALOG:
            return await self._handle_modal_dialog(perception, wait_seconds)
        elif unexpected == UnexpectedState.FOCUS_LOST:
            return await self._handle_focus_lost(perception, wait_seconds)
        elif unexpected == UnexpectedState.UI_CHANGED:
            return await self._handle_ui_changed(perception, wait_seconds)
        elif unexpected == UnexpectedState.PERMISSION_DENIED:
            return RecoveryDecision(
                should_retry=False,
                reason="Permission denied - requires user intervention",
                action="request_permission",
                wait_seconds=wait_seconds,
            )
        elif unexpected == UnexpectedState.APPLICATION_CRASH:
            return await self._handle_app_crash(wait_seconds)
        elif unexpected == UnexpectedState.APPLICATION_HANG:
            return await self._handle_app_hang(wait_seconds)
        elif unexpected == UnexpectedState.FILE_NOT_FOUND:
            return RecoveryDecision(
                should_retry=False,
                reason="File not found - requires user to specify location",
                action="ask_user_for_path",
                wait_seconds=wait_seconds,
            )
        elif unexpected == UnexpectedState.NETWORK_ERROR:
            return RecoveryDecision(
                should_retry=True,
                reason="Network error - retry after delay",
                action="retry_same",
                wait_seconds=wait_seconds,
            )
        else:
            # Unknown - try re-grounding with different method
            return RecoveryDecision(
                should_retry=True,
                reason=f"Unknown failure, attempting re-ground",
                action="retry_re_ground",
                wait_seconds=wait_seconds,
            )
    
    def _classify_unexpected(
        self, 
        perception: PerceptionSnapshot, 
        verification: VerificationResult
    ) -> UnexpectedState:
        """Classify the type of unexpected state from perception."""
        from .state import UnexpectedState, ControlInfo
        
        # Check for modal dialog (new window with modal flag)
        if perception.active_window.get("is_modal", False):
            return UnexpectedState.MODAL_DIALOG
        
        # Check for error dialog text in OCR
        error_keywords = ["error", "failed", "access denied", "permission", 
                         "not found", "cannot", "unable", "denied"]
        ocr_lower = perception.ocr_text.lower()
        if any(kw in ocr_lower for kw in error_keywords):
            if "permission" in ocr_lower or "access denied" in ocr_lower:
                return UnexpectedState.PERMISSION_DENIED
            if "not found" in ocr_lower or "file not found" in ocr_lower:
                return UnexpectedState.FILE_NOT_FOUND
            return UnexpectedState.MODAL_DIALOG
        
        # Check focus lost
        if perception.active_app != perception.active_window.get("app", ""):
            return UnexpectedState.FOCUS_LOST
        
        # Check app crash (process gone)
        # This would need process monitoring - simplified for now
        
        # Check UI changed significantly
        if perception.change_detected and perception.screenshot_diff > 0.5:
            return UnexpectedState.UI_CHANGED
        
        return UnexpectedState.UNKNOWN
    
    async def _handle_modal_dialog(
        self, 
        perception: PerceptionSnapshot, 
        wait_seconds: float
    ) -> RecoveryDecision:
        """Handle modal dialog recovery."""
        # Read dialog text from OCR
        dialog_text = perception.ocr_text[:200]
        
        # Determine appropriate action based on dialog content
        ocr_lower = perception.ocr_text.lower()
        
        if "save" in ocr_lower:
            return RecoveryDecision(
                should_retry=True,
                reason=f"Save dialog detected: {dialog_text}",
                action="click_save_button",
                new_grounding_method="OCR_TEXT",
                wait_seconds=wait_seconds,
            )
        elif "confirm" in ocr_lower or "are you sure" in ocr_lower:
            return RecoveryDecision(
                should_retry=True,
                reason=f"Confirmation dialog: {dialog_text}",
                action="click_confirm",
                new_grounding_method="OCR_TEXT",
                wait_seconds=wait_seconds,
            )
        elif "error" in ocr_lower or "failed" in ocr_lower:
            return RecoveryDecision(
                should_retry=False,
                reason=f"Error dialog: {dialog_text}",
                action="read_error_and_escalate",
                wait_seconds=wait_seconds,
            )
        else:
            # Generic dialog - try to close or cancel
            return RecoveryDecision(
                should_retry=True,
                reason=f"Unknown dialog: {dialog_text}",
                action="click_cancel_or_close",
                new_grounding_method="OCR_TEXT",
                wait_seconds=wait_seconds,
            )
    
    async def _handle_focus_lost(
        self, 
        perception: PerceptionSnapshot, 
        wait_seconds: float
    ) -> RecoveryDecision:
        """Handle focus lost to another application."""
        return RecoveryDecision(
            should_retry=True,
            reason=f"Focus lost to {perception.active_app}",
            action="refocus_target_window",
            new_grounding_method="UIA",
            wait_seconds=wait_seconds,
        )
    
    async def _handle_ui_changed(
        self, 
        perception: PerceptionSnapshot, 
        wait_seconds: float
    ) -> RecoveryDecision:
        """Handle significant UI change (app update, layout change)."""
        return RecoveryDecision(
            should_retry=True,
            reason="UI layout changed significantly",
            action="re_ground_with_ocr",
            new_grounding_method="OCR_TEXT",
            wait_seconds=wait_seconds,
        )
    
    async def _handle_app_crash(self, wait_seconds: float) -> RecoveryDecision:
        """Handle application crash."""
        return RecoveryDecision(
            should_retry=True,
            reason="Application appears to have crashed",
            action="restart_application",
            wait_seconds=wait_seconds,
        )
    
    async def _handle_app_hang(self, wait_seconds: float) -> RecoveryDecision:
        """Handle application hang."""
        return RecoveryDecision(
            should_retry=True,
            reason="Application not responding",
            action="force_close_and_restart",
            wait_seconds=wait_seconds,
        )