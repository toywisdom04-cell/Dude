"""Dialog and popup blocker detection + automatic recovery.

Watches for common blocking dialogs (replace file, access denied, connection timeout, etc.)
and automatically handles them to keep operations flowing."""

import time
import re
from typing import Optional, Dict, Any

class DialogBlocker:
    """Detects and recovers from common Windows blocking dialogs."""
    
    # Common dialog patterns and their handlers
    DIALOG_PATTERNS = {
        "replace_file": {
            "keywords": ["replace", "file", "already", "exists", "overwrite"],
            "buttons": ["yes", "no", "replace", "cancel"],
            "action": "click_yes",
            "description": "File replace confirmation"
        },
        "access_denied": {
            "keywords": ["access", "denied", "permission", "administrator"],
            "buttons": ["ok", "retry", "cancel"],
            "action": "click_ok",
            "description": "Access denied dialog"
        },
        "file_in_use": {
            "keywords": ["file", "use", "application", "close", "open"],
            "buttons": ["retry", "cancel", "ignore"],
            "action": "click_retry",
            "description": "File in use dialog"
        },
        "connection_timeout": {
            "keywords": ["connection", "timeout", "network", "failed"],
            "buttons": ["retry", "cancel", "ok"],
            "action": "click_retry",
            "description": "Connection timeout dialog"
        },
        "security_warning": {
            "keywords": ["security", "warning", "unsafe", "blocked", "allow"],
            "buttons": ["allow", "proceed", "yes", "ok"],
            "action": "click_yes",
            "description": "Security warning dialog"
        },
        "confirm_delete": {
            "keywords": ["delete", "confirm", "are you sure", "permanently"],
            "buttons": ["yes", "delete", "confirm"],
            "action": "click_yes",
            "description": "Delete confirmation dialog"
        },
    }

    def __init__(self, observer, tools_module):
        self.observer = observer
        self.tools = tools_module
        self._last_check = 0.0
        self._cooldown = 1.0  # Don't check more than every 1 second

    def detect_and_recover(self, screen_text: str, screen_elements: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Detect blocking dialogs from screen content and elements.
        
        Args:
            screen_text: OCR/UI text from current screen
            screen_elements: UI control map from screentree
            
        Returns:
            Recovery action dict if dialog found, None otherwise
        """
        now = time.time()
        if now - self._last_check < self._cooldown:
            return None
        self._last_check = now
        
        screen_lower = (screen_text or "").lower()
        
        # Detect dialog type
        matched_dialog = None
        for dialog_type, pattern in self.DIALOG_PATTERNS.items():
            keywords = pattern.get("keywords", [])
            if all(kw in screen_lower for kw in keywords[:2]):  # Match at least 2 keywords
                matched_dialog = dialog_type
                break
        
        if not matched_dialog:
            return None
        
        pattern = self.DIALOG_PATTERNS[matched_dialog]
        return {
            "dialog_type": matched_dialog,
            "description": pattern["description"],
            "action": pattern["action"],
            "buttons": pattern["buttons"],
            "confidence": 0.7,
        }

    def execute_recovery(self, recovery_info: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the recovery action for a detected dialog."""
        try:
            action = recovery_info.get("action")
            dialog_type = recovery_info.get("dialog_type")
            
            if action == "click_yes":
                # Click the "Yes", "Confirm", "Replace", or "Allow" button
                result = self.tools.press_hotkey(None, {"key": "alt+y"})
                if "ERROR" not in str(result):
                    return {"status": "OK", "action": action, "dialog": dialog_type}
                # Fallback: try Tab+Enter
                self.tools.press_hotkey(None, {"key": "tab"})
                self.tools.press_hotkey(None, {"key": "enter"})
                return {"status": "OK", "action": action, "dialog": dialog_type}
            
            elif action == "click_ok":
                # Click "OK" button
                result = self.tools.press_hotkey(None, {"key": "enter"})
                if "ERROR" not in str(result):
                    return {"status": "OK", "action": action, "dialog": dialog_type}
                return {"status": "OK", "action": action, "dialog": dialog_type}
            
            elif action == "click_retry":
                # Click "Retry" button
                result = self.tools.press_hotkey(None, {"key": "alt+r"})
                if "ERROR" not in str(result):
                    return {"status": "OK", "action": action, "dialog": dialog_type}
                # Fallback: Tab to Retry and press Enter
                self.tools.press_hotkey(None, {"key": "tab"})
                self.tools.press_hotkey(None, {"key": "enter"})
                return {"status": "OK", "action": action, "dialog": dialog_type}
            
            return {"status": "ERROR", "message": f"Unknown action: {action}"}
        
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

class UnexpectedStateDetector:
    """Detects unexpected UI states that block progress."""
    
    BLOCKING_STATES = {
        "save_dialog": ["do you want to save", "save changes", "discard changes"],
        "update_prompt": ["update available", "download update", "install update"],
        "error_message": ["error", "failed", "exception", "crash"],
        "blank_screen": ["loading", "please wait", "initializing"],
        "login_required": ["login", "sign in", "authenticate", "password"],
    }

    def __init__(self):
        self._last_state = ""
        self._state_since = 0.0

    def detect(self, screen_text: str, screen_elements: Dict[str, Any]) -> Optional[str]:
        """Detect unexpected states that might block progress."""
        screen_lower = (screen_text or "").lower()
        
        for state_type, keywords in self.BLOCKING_STATES.items():
            if any(kw in screen_lower for kw in keywords):
                if state_type != self._last_state:
                    self._last_state = state_type
                    self._state_since = time.time()
                    return state_type
                # Check if state persisted > 5 seconds
                if time.time() - self._state_since > 5.0:
                    return state_type
        
        self._last_state = ""
        return None

    def get_recovery_suggestion(self, state_type: str) -> Dict[str, Any]:
        """Suggest recovery action for detected state."""
        suggestions = {
            "save_dialog": {
                "action": "press_key",
                "key": "n",  # Usually "No" is default/safe
                "reason": "Dismiss save dialog without saving"
            },
            "update_prompt": {
                "action": "press_key",
                "key": "escape",
                "reason": "Dismiss update prompt"
            },
            "error_message": {
                "action": "press_key",
                "key": "enter",
                "reason": "Close error message"
            },
            "blank_screen": {
                "action": "wait_and_retry",
                "delay": 3.0,
                "reason": "Wait for app to finish loading"
            },
            "login_required": {
                "action": "cancel_operation",
                "reason": "Cannot proceed without login"
            },
        }
        return suggestions.get(state_type, {"action": "none"})
