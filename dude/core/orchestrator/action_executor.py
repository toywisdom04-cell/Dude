"""ActionExecutor - Grounds and executes structured actions.

Receives Action objects, grounds them against current perception,
executes via existing tools, and returns structured results.
"""
from __future__ import annotations

import time
import logging
import json
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum

from .state import (
    Action,
    TargetSpec,
    GroundingMethod,
    RiskLevel,
    ControlInfo,
    VerificationMethod,
    ExpectedResult,
    ActualResult,
)
from .perception import PerceptionSnapshot, PerceptionEngine

# Import existing tool execution
from core.tools import execute_tool, REGISTRY as TOOL_REGISTRY
from core.memory import Memory

log = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    """Result status of action execution."""
    NOT_GROUNDED = "not_grounded"
    PERMISSION_DENIED = "permission_denied"
    EXECUTION_FAILED = "execution_failed"
    EXECUTED = "executed"


@dataclass
class ActionExecutionResult:
    """Result of executing an action."""
    success: bool
    status: ExecutionStatus
    message: str
    error: str = ""
    grounded_method: Optional[GroundingMethod] = None
    grounded_coordinates: Optional[tuple[int, int]] = None
    grounded_confidence: float = 0.0
    actual_result: Optional[ActualResult] = None
    duration_seconds: float = 0.0


class ActionExecutor:
    """Executes structured actions with perception grounding."""
    
    # Actions that don't require UI grounding (can execute directly)
    NO_GROUND_ACTIONS = {
        "open_app", "open_application", "open_url",
        "run_powershell", "screenshot", "get_datetime",
        "list_running_apps", "list_directory", "search_files",
        "clipboard_read", "clipboard_write", "press_hotkey",
        "hotkey",
        "create_folder", "create_directory", "make_folder",
        "read_file", "write_file", "delete_path", "move_path", "copy_path", "search_files",
        "verify_file",
        "close_app", "close_application",
    }
    
    def __init__(
        self,
        perception: PerceptionEngine,
        memory: Optional[Memory] = None,
        min_confidence: float = 0.7,
    ):
        self.perception = perception
        self.memory = memory
        self.min_confidence = min_confidence
    
    async def execute(
        self,
        action: Action,
        perception: PerceptionSnapshot,
        permission_state=None,
    ) -> ActionExecutionResult:
        """Execute an action with full grounding pipeline."""
        start_time = time.time()
        
        # 1. Check permission
        if permission_state and hasattr(permission_state, 'value'):
            if permission_state.value == "denied":
                return ActionExecutionResult(
                    success=False,
                    status=ExecutionStatus.PERMISSION_DENIED,
                    message="Permission denied",
                    error="Permission denied by user/policy",
                    duration_seconds=time.time() - start_time,
                )
        
        # 2. Check perception freshness
        if not perception.is_fresh(max_age=5.0):
            return ActionExecutionResult(
                success=False,
                status=ExecutionStatus.EXECUTION_FAILED,
                message="Perception too stale for safe execution",
                error="Perception older than 5 seconds",
                duration_seconds=time.time() - start_time,
            )
        
        # 3. Determine if action requires UI grounding
        no_ground_actions = self.NO_GROUND_ACTIONS
        
        action_type = action.action_type.lower()
        requires_grounding = action_type not in no_ground_actions
        
        grounded = None
        if requires_grounding:
            # 3. Ground the action target
            grounded = self._ground_action(action, perception)
            
            if not grounded.control:
                return ActionExecutionResult(
                    success=False,
                    status=ExecutionStatus.NOT_GROUNDED,
                    message=f"Could not ground target: {action.target_description}",
                    error="No matching control found on screen",
                    duration_seconds=time.time() - start_time,
                )
            
            if grounded.confidence < self.min_confidence:
                return ActionExecutionResult(
                    success=False,
                    status=ExecutionStatus.NOT_GROUNDED,
                    message=f"Grounding confidence too low: {grounded.confidence:.2f}",
                    error=f"Confidence {grounded.confidence:.2f} below minimum {self.min_confidence}",
                    grounded_method=grounded.method,
                    grounded_coordinates=grounded.coordinates,
                    grounded_confidence=grounded.confidence,
                    duration_seconds=time.time() - start_time,
                )
        else:
            # For no-ground actions, create a minimal grounded action with coordinates if available
            coords = None
            if action.target.coordinates:
                coords = action.target.coordinates
            elif action.target.relative_coords and perception.window_bounds:
                wb = perception.window_bounds
                rx, ry = action.target.relative_coords
                coords = (int(wb.x + rx * wb.w), int(wb.y + ry * wb.h))
            
            grounded = GroundedAction(
                control=None,
                method=GroundingMethod.KEYBOARD_DIRECT if action_type in ("type_text", "hotkey") else GroundingMethod.ABSOLUTE_COORDS,
                coordinates=coords,
                confidence=1.0,
            )
        
        # 4. Execute the grounded action
        try:
            result = await self._execute_grounded(action, grounded, perception)
            result.duration_seconds = time.time() - start_time
            result.grounded_method = grounded.method
            result.grounded_coordinates = grounded.coordinates
            result.grounded_confidence = grounded.confidence
            return result
        except Exception as e:
            log.exception(f"Action execution failed: {e}")
            return ActionExecutionResult(
                success=False,
                status=ExecutionStatus.EXECUTION_FAILED,
                message=f"Execution error: {e}",
                error=str(e),
                grounded_method=grounded.method,
                grounded_coordinates=grounded.coordinates,
                grounded_confidence=grounded.confidence,
                duration_seconds=time.time() - start_time,
            )
    
    def _rows_to_rank_inputs(self, rows) -> list:
        """Normalize snapshot ControlInfo rows for the ranker."""
        from .disambiguation import RankInput
        out = []
        for c in rows or []:
            try:
                out.append(RankInput(
                    name=getattr(c, 'name', '') or "",
                    ctype=getattr(c, 'ctype', '') or "",
                    role=getattr(c, 'role', '') or "",
                    automation_id=getattr(c, 'automation_id', '') or "",
                    x=int(getattr(c, 'x', 0) or 0),
                    y=int(getattr(c, 'y', 0) or 0),
                    w=int(getattr(c, 'w', 0) or 0),
                    h=int(getattr(c, 'h', 0) or 0),
                    enabled=bool(getattr(c, 'enabled', True)),
                    visible=bool(getattr(c, 'visible', True)),
                    offscreen=False, ref=c))
            except Exception:
                continue
        return out

    def _disambiguate(self, rows, query: str, role_hint: str = ""):
        """Shared ranker entry: ordinal/type/reading-order/label picks.

        Returns (original_row_or_None, evidence). A role hint constrains
        the pool (roles are requirements); otherwise the ranker decides
        from the query alone. Never uses coordinates as a rule.
        """
        from .disambiguation import select_control
        pool = self._rows_to_rank_inputs(rows)
        if role_hint:
            rh = (role_hint or "").lower()
            constrained = [c for c in pool
                           if rh in (c.ctype or "").lower()
                           or rh in (c.role or "").lower()]
            if not constrained:
                # Role is a hard requirement (a ButtonControl request
                # must never land on a TreeControl): no pool, no pick.
                # Callers fall through to OCR/other methods as before.
                return None, {"reason": "role hard constraint unmet",
                              "role": role_hint}
            pool = constrained
        full_query = (query or "")
        if role_hint and role_hint.lower() not in full_query.lower():
            full_query = f"{full_query} {role_hint}"
        pick, ev = select_control(pool, full_query)
        if pick is not None:
            return pick.ref, ev
        return None, ev

    def _ground_action(
        self, 
        action: Action, 
        perception: PerceptionSnapshot
    ) -> "GroundedAction":
        """Ground action target against current perception."""
        # Priority order: UIA -> UIA_ROLE -> OCR_TEXT -> CV_TEMPLATE -> RELATIVE_COORDS -> ABSOLUTE_COORDS
        
        target = action.target
        action_type = action.action_type.lower()
        
        # Special handling for type_text: find the editable control in the target window
        if action_type in ("type_text", "type"):
            editable_ctrl = self._find_editable_control(perception)
            if editable_ctrl:
                return GroundedAction(
                    control=editable_ctrl,
                    method=GroundingMethod.UIA,
                    coordinates=editable_ctrl.rect.center(),
                    confidence=0.90,
                )
        
        # 1. UIA by control_id (highest confidence)
        if target.control_id:
            for ctrl in perception.controls:
                if ctrl.automation_id == target.control_id and ctrl.enabled and ctrl.visible:
                    return GroundedAction(
                        control=ctrl,
                        method=GroundingMethod.UIA,
                        coordinates=ctrl.rect.center(),
                        confidence=0.95,
                    )
        
        # 2. UIA by control_name. When a role was specified alongside
        # the name (e.g. "Save | ButtonControl"), it is a requirement, not
        # a tiebreaker: a lone substring hit of the wrong type must never
        # be acted on (it once steered a click at an unrelated toolbar
        # button whose label merely contained the target text).
        if target.control_name:
            matches = [
                c for c in perception.controls
                if target.control_name.lower() in c.name.lower() and c.enabled and c.visible
            ]
            if target.control_role:
                matches = [m for m in matches
                           if (getattr(m, 'role', '') or '') == target.control_role
                           or (getattr(m, 'ctype', '') or '') == target.control_role]
            # Exact name hits win over substring hits: "Clear" must not
            # land on "Clear entry", "Save" must not land on "Save As".
            # Substring matching stays as the fallback for partial names.
            exact = [m for m in matches
                     if (m.name or "").strip().lower()
                     == target.control_name.strip().lower()]
            if len(exact) == 1:
                return GroundedAction(
                    control=exact[0],
                    method=GroundingMethod.UIA,
                    coordinates=exact[0].rect.center(),
                    confidence=0.95,
                )
            use = exact if exact else matches
            if len(use) == 1:
                return GroundedAction(
                    control=use[0],
                    method=GroundingMethod.UIA,
                    coordinates=use[0].rect.center(),
                    confidence=0.90,
                )
            elif len(use) > 1:
                # Try to disambiguate by role
                if target.control_role:
                    role_matches = [m for m in use if m.role == target.control_role]
                    if len(role_matches) == 1:
                        return GroundedAction(
                            control=role_matches[0],
                            method=GroundingMethod.UIA_ROLE,
                            coordinates=role_matches[0].rect.center(),
                            confidence=0.85,
                        )
                # Generic semantic disambiguation (ordinal + type +
                # reading order + label/container): "second button" must
                # not depend on UIA tree order.
                pick, ev = self._disambiguate(
                    use, target.control_name or "",
                    role_hint=target.control_role or "")
                if pick is not None:
                    log.info(f"Disambiguated '{target.control_name}': "
                             f"{ev}")
                    return GroundedAction(
                        control=pick,
                        method=GroundingMethod.UIA,
                        coordinates=pick.rect.center(),
                        confidence=0.85,
                        evidence=ev,
                    )
                # Multiple matches - ambiguous
                log.warning(f"Ambiguous UIA match for '{target.control_name}': {len(use)} controls")
            else:
                # Zero name matches: an ordinal/type reference ("first
                # text field") still resolves against the type pool
                # instead of falling straight to OCR/pixels.
                pick, ev = self._disambiguate(
                    [c for c in perception.controls
                     if c.enabled and c.visible],
                    target.control_name or "",
                    role_hint=target.control_role or "")
                if pick is not None:
                    log.info(f"Disambiguated '{target.control_name}': "
                             f"{ev}")
                    return GroundedAction(
                        control=pick,
                        method=GroundingMethod.UIA,
                        coordinates=pick.rect.center(),
                        confidence=0.85,
                        evidence=ev,
                    )
        
        # 3. OCR text match
        if target.text_match:
            for region in perception.ocr_regions:
                if target.text_match.lower() in region.text.lower():
                    ctrl = ControlInfo(
                        name=region.text,
                        x=region.x,
                        y=region.y,
                        w=region.w,
                        h=region.h,
                        ctype="ocr_text",
                    )
                    return GroundedAction(
                        control=ctrl,
                        method=GroundingMethod.OCR_TEXT,
                        coordinates=region.center,
                        confidence=region.confidence,
                    )
        
        # 4. Relative coordinates (fraction of window)
        if target.relative_coords and perception.window_bounds:
            wb = perception.window_bounds
            rx, ry = target.relative_coords
            x = int(wb.x + rx * wb.w)
            y = int(wb.y + ry * wb.h)
            ctrl = ControlInfo(x=x, y=y, w=1, h=1, ctype="relative")
            return GroundedAction(
                control=ctrl,
                method=GroundingMethod.RELATIVE_COORDS,
                coordinates=(x, y),
                confidence=0.65,
            )
        
        # 5. Absolute coordinates (last resort)
        if target.coordinates:
            ctrl = ControlInfo(
                x=target.coordinates[0],
                y=target.coordinates[1],
                w=1, h=1,
                ctype="absolute",
            )
            return GroundedAction(
                control=ctrl,
                method=GroundingMethod.ABSOLUTE_COORDS,
                coordinates=target.coordinates,
                confidence=0.40,
            )
        
        # No grounding possible
        return GroundedAction(control=None, method=None, coordinates=None, confidence=0.0)
    
    async def _execute_grounded(
        self, 
        action: Action, 
        grounded: "GroundedAction",
        perception: PerceptionSnapshot
    ) -> ActionExecutionResult:
        """Execute the grounded action via appropriate tool."""
        action_type = action.action_type.lower()

        # Focus-free read: extract text through UIA ValuePattern without
        # touching focus, foreground, mouse, or keyboard. This is the
        # background-capable action; everything below needs the
        # foreground and stays foreground-only by design.
        if action_type == "read_text":
            return await self._execute_read(action, grounded, perception)

        # Verification-only step: nothing to actuate. Execution
        # succeeds trivially; the verification phase judges the path
        # (FILE_EXISTS) independently. Must never fabricate evidence.
        if action_type == "verify_file":
            return ActionExecutionResult(
                success=True,
                status=ExecutionStatus.EXECUTED,
                message="Verification-only step; file check runs "
                        "in verification phase",
                grounded_method=grounded.method,
                grounded_coordinates=grounded.coordinates,
                grounded_confidence=grounded.confidence,
            )

        # Map action types to tool names
        tool_map = {
            "click": "ui_click",
            "double_click": "ui_click",  # with double flag
            "type": "type_text",
            "hotkey": "press_hotkey",
            "scroll": "scroll_screen",
            "drag": "drag",
            "open_application": "open_app",
            "close_application": "close_app",
            "close_app": "close_app",
            "window_operation": "window_action",
            "file_operation": "run_powershell",  # generic
            "screenshot": "screenshot",
            "scroll_screen": "scroll_screen",
            "ui_click": "ui_click",
            "ui_scan": "ui_scan",
        }
        
        tool_name = tool_map.get(action_type, action_type)
        
        if tool_name not in TOOL_REGISTRY:
            return ActionExecutionResult(
                success=False,
                status=ExecutionStatus.EXECUTION_FAILED,
                message=f"Unknown action type: {action_type}",
                error=f"No tool registered for '{tool_name}'",
            )
        
        # Safety: input actions are only valid while the window they were
        # planned against is STILL the foreground window. Otherwise clicks
        # land (and keystrokes flow into) whatever the user just switched
        # to. Fail safe (recovery re-grounds) instead of acting blind.
        # open/close/read are exempt: opens change the foreground by
        # design, closes destroy their window, reads never touch input.
        if action_type not in ("open_app", "open_application",
                               "close_app", "close_application",
                               "read_text"):
            try:
                import win32gui as _wg
                _fg = _wg.GetForegroundWindow()
            except Exception:
                _fg = 0
            _exp_hwnd = (getattr(perception, 'active_window', None)
                         or {}).get("hwnd")
            if (isinstance(_exp_hwnd, int)
                    and not isinstance(_exp_hwnd, bool)
                    and _fg and _fg != _exp_hwnd):
                return ActionExecutionResult(
                    success=False,
                    status=ExecutionStatus.NOT_GROUNDED,
                    message="Foreground HWND changed since planning "
                            f"(expected {_exp_hwnd}, now {_fg})",
                    error="Stale window - foreground moved",
                    grounded_method=grounded.method,
                    grounded_coordinates=grounded.coordinates,
                    grounded_confidence=grounded.confidence,
                )
        if grounded.coordinates is not None:
            try:
                import win32gui
                fg_hwnd = win32gui.GetForegroundWindow()
                fg_now = win32gui.GetWindowText(fg_hwnd)
                fg_rect = win32gui.GetWindowRect(fg_hwnd)
            except Exception:
                fg_hwnd, fg_now, fg_rect = 0, "", None
            expected_title = (getattr(perception, 'active_window', None)
                                or {}).get("title", "")
            if expected_title and fg_now != expected_title:
                return ActionExecutionResult(
                    success=False,
                    status=ExecutionStatus.NOT_GROUNDED,
                    message="Foreground window changed since grounding "
                            f"(was {expected_title!r}, now {fg_now!r})",
                    error="Stale grounding - foreground moved",
                    grounded_method=grounded.method,
                    grounded_coordinates=grounded.coordinates,
                    grounded_confidence=grounded.confidence,
                )
            wb = getattr(perception, 'window_bounds', None)
            if wb is not None and fg_rect is not None:
                try:
                    moved = (abs(wb.x - fg_rect[0]) > 10
                             or abs(wb.y - fg_rect[1]) > 10)
                except Exception:
                    moved = False
                if moved:
                    return ActionExecutionResult(
                        success=False,
                        status=ExecutionStatus.NOT_GROUNDED,
                        message="Window moved since grounding "
                                f"(was ({wb.x},{wb.y}), now {fg_rect[:2]})",
                        error="Stale grounding - window moved",
                        grounded_method=grounded.method,
                        grounded_coordinates=grounded.coordinates,
                        grounded_confidence=grounded.confidence,
                    )

        # Safety: keyboard actions land wherever OS input focus is, which
        # is NOT necessarily the foreground window the perception snapshot
        # describes. Refuse to type hotkeys/text into a foreign process;
        # recovery re-runs the step once focus is back where it belongs.
        # The expectation is task-anchored (the app this plan operates on),
        # falling back to the snapshot's foreground app when the plan
        # carries none: comparing against the live foreground alone would
        # pass vacuously whenever both drift together.
        if action_type in ("type_text", "type", "hotkey"):
            try:
                import uiautomation as auto
                import psutil
                with auto.UIAutomationInitializerInThread():
                    focused = auto.GetFocusedControl()
                focused_name = (psutil.Process(
                    getattr(focused, 'ProcessId', 0)).name() or '').lower()
                if focused_name.endswith('.exe'):
                    focused_name = focused_name[:-4]
                want_app = ((getattr(getattr(action, 'expected_result',
                                             None),
                                      'process_name', None)
                              or getattr(perception, 'active_app', None)
                              or '').lower())
                # str-only: Mock snapshots in unit tests must skip this.
                if not isinstance(want_app, str):
                    want_app = ""
                if want_app.endswith('.exe'):
                    want_app = want_app[:-4]
                if want_app and focused_name and focused_name != want_app:
                    return ActionExecutionResult(
                        success=False,
                        status=ExecutionStatus.NOT_GROUNDED,
                        message="Input focus is in "
                                f"{focused_name!r}, not {want_app!r}",
                        error="Stale focus - keystrokes would land elsewhere",
                        grounded_method=grounded.method,
                        grounded_coordinates=grounded.coordinates,
                        grounded_confidence=grounded.confidence,
                    )
            except Exception:
                pass  # best effort; absence of UIA must not block typing

        # Keystrokes land in the focused control: refuse to type unless
        # focus is observably inside an editable control. A process-level
        # check already ran above; this is the control-level counterpart
        # (e.g. file list focused instead of the address bar after a
        # focus race). Unknown focus fails open (logged); known-bad
        # focus fails safe into recovery.
        if action_type in ("type_text", "type"):
            # First, click on the grounded control to focus it
            if grounded.coordinates:
                try:
                    import pyautogui
                    pyautogui.click(grounded.coordinates[0], grounded.coordinates[1])
                    time.sleep(0.2)  # Wait for focus to settle
                except Exception as e:
                    log.warning(f"Failed to focus editable control before typing: {e}")
            
            try:
                import uiautomation as auto
                with auto.UIAutomationInitializerInThread():
                    _fc = auto.GetFocusedControl()
                _fctype = getattr(_fc, "ControlTypeName", "") or ""
                _fcname = getattr(_fc, "Name", "") or ""
                log.info(f"Typing into focused {_fctype} {_fcname[:40]!r}")
                if _fctype and _fctype not in ("EditControl",
                                               "DocumentControl"):
                    return ActionExecutionResult(
                        success=False,
                        status=ExecutionStatus.NOT_GROUNDED,
                        message=f"Focused control is {_fctype} "
                                f"{_fcname[:40]!r}, not an editable control",
                        error="Refusing to type outside editable controls",
                        grounded_method=grounded.method,
                        grounded_coordinates=grounded.coordinates,
                        grounded_confidence=grounded.confidence,
                    )
            except Exception as e:
                log.warning(f"Focus check unavailable, proceeding: {e}")

        # Build tool arguments based on action type and grounded target
        args = self._build_tool_args(action, grounded)
        
        # Execute via existing tool system
        if not self.memory:
            # For testing without memory
            class MockMemory:
                pass
            mem = MockMemory()
        else:
            mem = self.memory
        
        try:
            tool_result = execute_tool(tool_name, json.dumps(args), mem, ask_user=lambda *a: True)
            
            # Parse tool result - tools signal failure with "ERROR: ..." or "DENIED: ..."
            if tool_result.startswith("ERROR:") or tool_result.startswith("DENIED:"):
                return ActionExecutionResult(
                    success=False,
                    status=ExecutionStatus.EXECUTION_FAILED,
                    message=tool_result,
                    error=tool_result,
                )
            else:
                actual = ActualResult(
                    uia_property=action.expected_result.uia_property,
                    uia_value=action.expected_result.uia_value,
                    text_found=action.expected_result.text_expected,
                    file_exists=action.expected_result.file_path is not None,
                )
                return ActionExecutionResult(
                    success=True,
                    status=ExecutionStatus.EXECUTED,
                    message=tool_result,
                    actual_result=actual,
                )
        except Exception as e:
            log.exception(f"Tool execution failed: {e}")
            return ActionExecutionResult(
                success=False,
                status=ExecutionStatus.EXECUTION_FAILED,
                message=f"Tool error: {e}",
                error=str(e),
            )
    
    async def _execute_read(
        self,
        action: Action,
        grounded: "GroundedAction",
        perception: PerceptionSnapshot,
    ) -> ActionExecutionResult:
        """Read a grounded control's text without touching input focus.

        Re-fetches the live UIA element scoped to the window the
        perception snapshot describes (pinned background window or the
        foreground window), then reads ValuePattern. Returns the text in
        both message and actual_result for verification and reporting.
        """
        target = action.target
        text = None
        try:
            import uiautomation as auto
            active = getattr(perception, "active_window", None) or {}
            hwnd = active.get("hwnd")
            with auto.UIAutomationInitializerInThread():
                if isinstance(hwnd, int) and not isinstance(hwnd, bool):
                    try:
                        root = auto.ControlFromHandle(hwnd)
                    except Exception:
                        root = None
                else:
                    root = None
                if root is None:
                    try:
                        root = auto.GetForegroundControl()
                    except Exception:
                        root = None
                # Ownership guard: never read from a stranger window. If
                # the live root is identifiably NOT the snapshot's window
                # (foreground moved between grounding and reading, or the
                # snapshot HWND went stale), fail loudly so recovery
                # re-grounds instead of returning another app's text as
                # if it were the target. Unknown handles (0) stay
                # permissive to avoid breaking exotic frames.
                try:
                    _exp_hwnd = active.get("hwnd") if isinstance(
                        active, dict) else 0
                    _act_hwnd = 0
                    if root is not None:
                        try:
                            _act_hwnd = int(root.NativeWindowHandle or 0)
                        except Exception:
                            _act_hwnd = 0
                    if isinstance(_exp_hwnd, int) and _exp_hwnd and _act_hwnd \
                            and _act_hwnd != _exp_hwnd:
                        return ActionExecutionResult(
                            success=False,
                            status=ExecutionStatus.EXECUTION_FAILED,
                            message="Read refused: window changed during "
                                    f"read (expected HWND {_exp_hwnd}, live "
                                    f"root {_act_hwnd})",
                            error="foreground moved between grounding and "
                                  "reading; re-ground required",
                            grounded_method=grounded.method,
                            grounded_coordinates=grounded.coordinates,
                            grounded_confidence=grounded.confidence,
                        )
                except Exception:
                    pass
                element = None
                if root is not None:
                    wanted_id = (target.control_id or "").strip()
                    wanted_name = (target.control_name or "").lower()
                    wanted_role = (target.control_role or "").lower()

                    # Aid exact hits stay instant; everything else goes
                    # through ONE collection + the shared ranker so live
                    # reads resolve ambiguity exactly like grounding does
                    # (ordinal + type + reading order + label context) —
                    # never first-DFS-hit-wins.
                    from .disambiguation import RankInput, select_control

                    collected: list = []

                    def collect(node, depth, parent_name, prev_text):
                        if depth > 8:
                            return
                        try:
                            kids = node.GetChildren()
                        except Exception:
                            return
                        prev = prev_text
                        for idx, ch in enumerate(kids):
                            try:
                                ctc = (getattr(ch, 'ControlTypeName', '')
                                       or '')
                                nm = (ch.Name or "")
                                aid = ""
                                try:
                                    aid = ch.AutomationId or ""
                                except Exception:
                                    pass
                                if wanted_id and aid == wanted_id:
                                    collected.append(('aid', ch))
                                    return
                                try:
                                    r = ch.BoundingRectangle
                                    x, y, w, h = (int(r.left), int(r.top),
                                                  int(r.width()),
                                                  int(r.height()))
                                except Exception:
                                    x = y = w = h = 0
                                try:
                                    en = bool(ch.IsEnabled)
                                except Exception:
                                    en = True
                                try:
                                    off = bool(ch.IsOffscreen)
                                except Exception:
                                    off = False
                                try:
                                    pctl = ch.GetParentControl()
                                    pname = (pctl.Name or '')[:60] \
                                        if pctl is not None else ''
                                except Exception:
                                    pname = parent_name
                                nearby = (prev or '')
                                if ctc in ("PaneControl", "GroupControl",
                                           "CustomControl", "WindowControl",
                                           "TabControl", "ListControl",
                                           "MenuControl", "MenuBarControl"):
                                    collected.append(RankInput(
                                        name=nm, ctype=ctc,
                                        automation_id=aid,
                                        x=x, y=y, w=w, h=h, enabled=en,
                                        visible=not off, offscreen=off,
                                        parent_name=pname or parent_name,
                                        nearby_text=nearby,
                                        sibling_index=idx, ref=ch))
                                    collect(ch, depth + 1,
                                            pname or nm or parent_name,
                                            '')
                                    prev = ''
                                else:
                                    collected.append(RankInput(
                                        name=nm, ctype=ctc,
                                        automation_id=aid,
                                        x=x, y=y, w=w, h=h, enabled=en,
                                        visible=not off, offscreen=off,
                                        parent_name=pname or parent_name,
                                        nearby_text=nearby,
                                        sibling_index=idx, ref=ch))
                                    if ctc in ("TextControl", "StaticControl",
                                               "LabelControl") and nm.strip():
                                        prev = nm.strip()[:60]
                            except Exception:
                                continue

                    collect(root, 0, '', '')
                    if not collected:
                        log.warning(
                            "Live-read collected zero candidates from a "
                            "live root: walker collected nothing (check "
                            "RankInput construction / COM errors swallowed "
                            "per-child).")
                    for item in collected:
                        if isinstance(item, tuple):
                            element = item[1]
                            break
                    if element is None:
                        inputs = [c for c in collected
                                  if not isinstance(c, tuple)]
                        if wanted_role:
                            constrained = [
                                c for c in inputs
                                if wanted_role in (c.ctype or '').lower()
                                or wanted_role in (c.role or '').lower()]
                            if not constrained:
                                log.info(f"Live-read role constraint unmet "
                                         f"for '{target.control_name}' "
                                         f"(role {wanted_role})")
                                inputs = []
                            else:
                                inputs = constrained
                        pick, ev = select_control(inputs, wanted_name)
                        if pick is not None:
                            element = pick.ref
                            log.info(f"Live-read disambiguated "
                                     f"'{target.control_name}': {ev}")
                        else:
                            log.info(f"Live-read ambiguous for "
                                     f"'{target.control_name}': {ev}")
                if element is not None:
                    try:
                        text = element.GetValuePattern().Value
                    except Exception:
                        text = None
                    if text is None:
                        # Labels, status texts, and display readouts are
                        # TextControls with no ValuePattern; their Name
                        # IS the text. Fall back generically so reads
                        # work on any app's visible strings.
                        try:
                            text = element.Name or None
                        except Exception:
                            text = None
        except Exception as e:
            log.warning(f"UIA read failed: {e}")
            text = None

        if text is None:
            return ActionExecutionResult(
                success=False,
                status=ExecutionStatus.EXECUTION_FAILED,
                message="Could not read text: control exposes no value",
                error="ValuePattern unavailable on grounded control",
                grounded_method=grounded.method,
                grounded_coordinates=grounded.coordinates,
                grounded_confidence=grounded.confidence,
            )
        text = str(text)
        if len(text) > 20000:
            text = text[:20000] + "…[truncated]"
        return ActionExecutionResult(
            success=True,
            status=ExecutionStatus.EXECUTED,
            message=text,
            actual_result=ActualResult(text_found=text),
            grounded_method=grounded.method,
            grounded_coordinates=grounded.coordinates,
            grounded_confidence=grounded.confidence,
        )

    def _build_tool_args(self, action: Action, grounded: "GroundedAction") -> dict:
        """Build tool arguments from action and grounded target."""
        action_type = action.action_type.lower()
        args = {}
        
        if action_type in ("click", "ui_click", "double_click"):
            args["x"] = grounded.coordinates[0] if grounded.coordinates else 0
            args["y"] = grounded.coordinates[1] if grounded.coordinates else 0
            if action_type == "double_click":
                args["double"] = True
        
        elif action_type in ("type", "type_text"):
            args["text"] = action.target.text_match or ""
            # Focus first if needed
            if grounded.coordinates:
                args["x"] = grounded.coordinates[0]
                args["y"] = grounded.coordinates[1]
        
        elif action_type == "hotkey":
            args["combo"] = action.target.text_match or ""
            args["keys"] = action.target.text_match or ""
        
        elif action_type in ("create_folder", "create_directory", "make_folder"):
            args["path"] = action.target.text_match or ""
        
        elif action_type == "scroll":
            args["direction"] = "down"
            args["amount"] = 3
        
        elif action_type in ("open_application", "open_app"):
            args["name"] = action.target.text_match or action.target.control_name or ""
            # Phase 5 window targeting, decided at plan time: an exact HWND
            # reuses one chosen window; fresh_window isolates the task in a
            # new one. Both replace name-based guessing (including the old
            # per-app special case this supersedes).
            if getattr(action.target, "hwnd", None):
                args["hwnd"] = action.target.hwnd
            if getattr(action.target, "fresh_window", False):
                args["new"] = True
        
        elif action_type in ("close_application", "close_app"):
            args["name"] = action.target.text_match or action.target.control_name or ""
        
        elif action_type == "screenshot":
            args["path"] = ""
        
        return args

    def _find_editable_control(self, perception: PerceptionSnapshot):
        """Find an editable control in the current perception.
        Returns the first editable control found, or None."""
        editable_types = ("EditControl", "DocumentControl", "PaneControl")
        # For Notepad, the text area is a DocumentControl
        # ControlInfo doesn't have ClassName attribute, use ctype
        for ctrl in perception.controls:
            if ctrl.enabled and ctrl.visible:
                ctype = ctrl.ctype
                if ctype in editable_types:
                    return ctrl
        return None


@dataclass
class GroundedAction:
    """Result of grounding an action target."""
    control: Optional[ControlInfo]
    method: Optional[GroundingMethod]
    coordinates: Optional[tuple[int, int]]
    confidence: float
    # Semantic evidence for WHY this control was chosen (ordinal,
    # label/container context, score). Recorded by the disambiguator;
    # coordinates remain the only action channel.
    evidence: dict = field(default_factory=dict)