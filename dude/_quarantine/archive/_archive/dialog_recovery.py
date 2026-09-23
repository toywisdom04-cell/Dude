"""Integration point: use dialog blocker in tool execution."""

import logging

log = logging.getLogger("dude")

def check_and_recover_from_blocker(observer, dialog_blocker, tools_module):
    """Check for blocking dialogs and recover if found."""
    try:
        # Get latest screen observation from observer
        if not observer or not observer._latest_shot:
            return None
        
        # Try to extract text from latest screenshot
        try:
            from core.tools import read_screen_text
            screen_text = read_screen_text(None, {})
            if isinstance(screen_text, str) and screen_text.startswith("OK:"):
                screen_text = screen_text[3:].strip()
        except Exception:
            screen_text = ""
        
        # Detect blocking dialog
        recovery_info = dialog_blocker.detect_and_recover(screen_text, {})
        if not recovery_info:
            return None
        
        log.info(f"Dialog blocker: Detected {recovery_info.get('dialog_type')} - {recovery_info.get('description')}")
        
        # Execute recovery
        result = dialog_blocker.execute_recovery(recovery_info)
        if result.get("status") == "OK":
            import time
            time.sleep(0.5)  # Let dialog close
            log.info(f"Dialog blocker: Recovered from {recovery_info.get('dialog_type')}")
            return result
        
        return None
    except Exception as e:
        log.debug(f"Dialog blocker check failed: {e}")
        return None
