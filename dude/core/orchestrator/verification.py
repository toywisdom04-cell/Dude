"""VerificationEngine - Verifies action outcomes against expectations.

Every action MUST have a verification method. Failed verification
triggers recovery rather than silent continuation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .state import (
    Action,
    VerificationMethod,
    VerificationResult,
    ExpectedResult,
    ActualResult,
    PerceptionSnapshot,
    ControlInfo,
)
from .perception import PerceptionEngine

log = logging.getLogger(__name__)


class VerificationEngine:
    """Verifies action outcomes using multiple verification methods."""
    
    def __init__(self, perception: PerceptionEngine):
        self.perception = perception
    
    async def verify(
        self,
        action: Action,
        expected: ExpectedResult,
        perception_before: PerceptionSnapshot,
        perception_after: PerceptionSnapshot,
        actual_result: Optional[ActualResult] = None,
    ) -> VerificationResult:
        """Verify action outcome using the specified method."""
        method = action.verification_method
        
        try:
            if method == VerificationMethod.UIA_STATE_CHANGE:
                return await self._verify_uia_state_change(action, expected, perception_after)
            elif method == VerificationMethod.OCR_TEXT_APPEARED:
                return await self._verify_ocr_appeared(action, expected, perception_after)
            elif method == VerificationMethod.OCR_TEXT_DISAPPEARED:
                return await self._verify_ocr_disappeared(action, expected, perception_before, perception_after)
            elif method == VerificationMethod.FILE_EXISTS:
                return await self._verify_file_exists(action, expected)
            elif method == VerificationMethod.WINDOW_APPEARED:
                return await self._verify_window_appeared(action, expected, perception_after)
            elif method == VerificationMethod.WINDOW_DISAPPEARED:
                return await self._verify_window_disappeared(action, expected, perception_before, perception_after)
            elif method == VerificationMethod.PROCESS_STATE:
                return await self._verify_process_state(action, expected, perception_after)
            elif method == VerificationMethod.CLIPBOARD_CONTENT:
                return await self._verify_clipboard(action, expected)
            elif method == VerificationMethod.SCREEN_DELTA:
                return await self._verify_screen_delta(action, expected, perception_before, perception_after)
            elif method == VerificationMethod.FOCUSED_CONTROL:
                return await self._verify_focused(action, expected, perception_after)
            elif method == VerificationMethod.TEXT_READ:
                return await self._verify_text_read(action, expected, actual_result)
            elif method == VerificationMethod.CUSTOM:
                return await self._verify_custom(action, expected, perception_after)
            else:
                return VerificationResult(
                    success=False,
                    method=method,
                    evidence=f"Unknown verification method: {method}",
                    confidence=0.0,
                )
        except Exception as e:
            log.exception(f"Verification failed: {e}")
            return VerificationResult(
                success=False,
                method=method,
                evidence=f"Verification error: {e}",
                confidence=0.0,
            )
    
    async def _verify_uia_state_change(
        self, 
        action: Action, 
        expected: ExpectedResult, 
        perception: PerceptionSnapshot
    ) -> VerificationResult:
        """Verify UIA control state changed as expected."""
        if not expected.uia_property or expected.uia_value is None:
            return VerificationResult(
                success=False,
                method=VerificationMethod.UIA_STATE_CHANGE,
                evidence="Expected UIA property and value required",
                confidence=0.0,
            )
        
        # Find control
        ctrl = self._find_control(action.target, perception)
        if not ctrl:
            return VerificationResult(
                success=False,
                method=VerificationMethod.UIA_STATE_CHANGE,
                evidence=f"Control not found: {action.target_description}",
                confidence=0.0,
            )
        
        # Check property
        actual_value = getattr(ctrl, expected.uia_property, None)
        success = actual_value == expected.uia_value
        
        return VerificationResult(
            success=success,
            method=VerificationMethod.UIA_STATE_CHANGE,
            evidence=f"{expected.uia_property}: {actual_value} (expected {expected.uia_value})",
            confidence=0.9 if success else 0.0,
            expected=expected,
            actual=ActualResult(uia_property=expected.uia_property, uia_value=actual_value),
        )
    
    async def _verify_ocr_appeared(
        self, 
        action: Action, 
        expected: ExpectedResult, 
        perception: PerceptionSnapshot
    ) -> VerificationResult:
        """Verify expected text appeared in OCR."""
        if not expected.text_expected:
            return VerificationResult(
                success=False,
                method=VerificationMethod.OCR_TEXT_APPEARED,
                evidence="Expected text required for OCR verification",
                confidence=0.0,
            )
        
        found = expected.text_expected.lower() in perception.ocr_text.lower()
        scope = "full screen"
        coords = getattr(action.target, 'coordinates', None)
        if not found and coords:
            # Fullscreen OCR routinely misses small editor text (the capture
            # is dominated by whatever window has the largest fonts, and
            # Tesseract confuses glyphs like 5/S at screen scale). Retry OCR
            # on a native-resolution crop around the action's screen
            # location: same SEE evidence, tighter aperture.
            shot = getattr(perception, 'screenshot', None)
            if shot:
                try:
                    from PIL import Image
                    from io import BytesIO
                    from core.ocr import ocr_image
                    img = Image.open(BytesIO(shot))
                    w, h = img.size
                    cx, cy = int(coords[0]), int(coords[1])
                    side = 700
                    box = (max(0, cx - side // 2), max(0, cy - side // 2),
                           min(w, cx + side // 2), min(h, cy + side // 2))
                    if box[2] - box[0] > 64 and box[3] - box[1] > 64:
                        crop_text = ocr_image(img.crop(box)) or ""
                        if expected.text_expected.lower() in crop_text.lower():
                            found = True
                            scope = (f"crop around ({cx},{cy}) "
                                     f"{box[2]-box[0]}x{box[3]-box[1]}")
                except Exception as e:
                    log.warning("OCR crop fallback failed: %s", e)
        if not found and (action.action_type or "").lower() in (
                "type_text", "type"):
            # Typing verification deserves the control's own ground truth:
            # read the editable control under the action point through UIA
            # ValuePattern (read-only, no focus change). OCR glyph confusion
            # must never fail a keystroke that verifiably landed.
            uia_text = self._read_editable_at(perception, coords)
            if (uia_text is not None and expected.text_expected.lower()
                    in uia_text.lower()):
                found = True
                scope = "uia-value foreground editor"

        if not found:
            # Log the actual OCR content on failure: distinguishes "text
            # genuinely absent" from "capture returned nothing usable".
            log.warning(
                "OCR verification missed '%s' (ocr_len=%d, head=%r)",
                expected.text_expected, len(perception.ocr_text or ""),
                (perception.ocr_text or "")[:120])

        return VerificationResult(
            success=found,
            method=VerificationMethod.OCR_TEXT_APPEARED,
            evidence=f"Text '{expected.text_expected}' "
                     f"{'found' if found else 'not found'} in OCR ({scope})",
            confidence=(0.85 if scope == "full screen" else 0.8)
                       if found else 0.0,
            expected=expected,
            actual=ActualResult(text_found=expected.text_expected if found else None),
        )
    
    @staticmethod
    def _read_editable_at(perception, coords) -> Optional[str]:
        """Read-only UIA ValuePattern of the editable under a screen point.

        Scoped to the window the perception snapshot describes (pinned
        HWND or foreground). Returns None when no editable control covers
        the point or it exposes no value. Never changes focus.
        """
        try:
            import uiautomation as auto
            active = getattr(perception, "active_window", None) or {}
            hwnd = active.get("hwnd")
            with auto.UIAutomationInitializerInThread():
                root = None
                if isinstance(hwnd, int) and not isinstance(hwnd, bool):
                    try:
                        root = auto.ControlFromHandle(hwnd)
                    except Exception:
                        root = None
                if root is None:
                    try:
                        root = auto.GetForegroundControl()
                    except Exception:
                        return None
                cx, cy = int(coords[0]), int(coords[1])
                best = [None, -1]

                def visit(node, depth):
                    if depth > 8:
                        return
                    try:
                        kids = node.GetChildren()
                    except Exception:
                        return
                    for ch in kids:
                        try:
                            ctc = (getattr(ch, "ControlTypeName", "") or "")
                            if ctc in ("DocumentControl", "EditControl",
                                       "TextControl"):
                                try:
                                    r = ch.BoundingRectangle
                                    if (r.left <= cx < r.right
                                            and r.top <= cy < r.bottom):
                                        area = ((r.right - r.left)
                                                * (r.bottom - r.top))
                                        if (best[0] is None
                                                or area < best[1]):
                                            best[0] = ch
                                            best[1] = area
                                except Exception:
                                    pass
                            if ctc in ("PaneControl", "GroupControl",
                                       "CustomControl", "WindowControl",
                                       "TabControl", "TitleBarControl",
                                       "MenuControl", "MenuBarControl",
                                       "ToolBarControl", "HeaderControl"):
                                visit(ch, depth + 1)
                        except Exception:
                            pass

                visit(root, 0)
                if best[0] is None:
                    return None
                try:
                    return best[0].GetValuePattern().Value or ""
                except Exception:
                    return None
        except Exception as e:
            log.warning(f"UIA editable read failed: {e}")
            return None

    async def _verify_ocr_disappeared(
        self, 
        action: Action, 
        expected: ExpectedResult, 
        before: PerceptionSnapshot,
        after: PerceptionSnapshot
    ) -> VerificationResult:
        """Verify expected text disappeared from OCR."""
        if not expected.text_expected:
            return VerificationResult(
                success=False,
                method=VerificationMethod.OCR_TEXT_DISAPPEARED,
                evidence="Expected text required for OCR verification",
                confidence=0.0,
            )
        
        before_found = expected.text_expected.lower() in before.ocr_text.lower()
        after_found = expected.text_expected.lower() in after.ocr_text.lower()
        success = before_found and not after_found
        
        return VerificationResult(
            success=success,
            method=VerificationMethod.OCR_TEXT_DISAPPEARED,
            evidence=f"Text '{expected.text_expected}' {'disappeared' if success else 'still present'}",
            confidence=0.85 if success else 0.0,
        )
    
    async def _verify_file_exists(
        self, 
        action: Action, 
        expected: ExpectedResult
    ) -> VerificationResult:
        """Verify file exists at expected path."""
        import os
        
        if not expected.file_path:
            return VerificationResult(
                success=False,
                method=VerificationMethod.FILE_EXISTS,
                evidence="Expected file path required",
                confidence=0.0,
            )
        
        exists = os.path.exists(expected.file_path)
        
        return VerificationResult(
            success=exists,
            method=VerificationMethod.FILE_EXISTS,
            evidence=f"File '{expected.file_path}' {'exists' if exists else 'not found'}",
            confidence=0.95 if exists else 0.0,
            expected=expected,
            actual=ActualResult(file_exists=exists),
        )
    
    async def _verify_window_appeared(
        self, 
        action: Action, 
        expected: ExpectedResult, 
        perception: PerceptionSnapshot
    ) -> VerificationResult:
        """Verify expected window appeared."""
        if not expected.window_title and not expected.process_name:
            return VerificationResult(
                success=False,
                method=VerificationMethod.WINDOW_APPEARED,
                evidence="Expected window title or process name required",
                confidence=0.0,
            )
        
        current_app = perception.active_app
        current_window = perception.active_window.get("title", "")

        # Identity check is canonical (see app_instances): normal
        # processes match by executable basename; UWP-hosted windows
        # (identical host executable for every Store app) match by
        # window title instead.
        from .app_instances import app_identity_matches
        app_match = (not expected.process_name
                     or app_identity_matches(expected.process_name,
                                             current_app, current_window))
        
        title_match = not expected.window_title or expected.window_title.lower() in current_window.lower()
        if not title_match and expected.window_title:
            # Folded fallback: "note pad"/"Notepad." must match titles
            # containing "Notepad" (voice-transcript spacing/punctuation).
            import re as _re
            fold = lambda s: _re.sub(r'[^a-z0-9]', '', (s or "").lower())
            title_match = bool(fold(expected.window_title)) and \
                fold(expected.window_title) in fold(current_window)
        if not title_match and expected.window_title and expected.process_name:
            # The plan asserted no title beyond the app name itself
            # (open_app echoes the app into both fields): windows whose
            # titles never contain the app name (e.g. Terminal shows
            # shell titles) verify by app identity alone. Steps with a
            # genuinely distinct title keep the strict check above.
            import re as _re2
            fold2 = lambda s: _re2.sub(r'[^a-z0-9]', '', (s or "").lower())
            if fold2(expected.window_title) == fold2(expected.process_name):
                title_match = True
        
        success = app_match and title_match
        
        return VerificationResult(
            success=success,
            method=VerificationMethod.WINDOW_APPEARED,
            evidence=f"Window {'matched' if success else 'not matched'}: app={current_app}, title={current_window}",
            confidence=0.9 if success else 0.0,
            expected=expected,
            actual=ActualResult(window_title=current_window, process_name=current_app),
        )
    
    async def _verify_window_disappeared(
        self, 
        action: Action, 
        expected: ExpectedResult, 
        before: PerceptionSnapshot,
        after: PerceptionSnapshot
    ) -> VerificationResult:
        """Verify expected window disappeared."""
        before_app = before.active_app
        before_title = before.active_window.get("title", "")
        after_app = after.active_app
        after_title = after.active_window.get("title", "")
        
        # Window disappeared if it was there before and not there now
        before_match = True
        if expected.process_name:
            before_match = expected.process_name.lower() in before_app.lower()
        if expected.window_title:
            before_match = before_match and expected.window_title.lower() in before_title.lower()
        
        after_match = True
        if expected.process_name:
            after_match = expected.process_name.lower() in after_app.lower()
        if expected.window_title:
            after_match = after_match and expected.window_title.lower() in after_title.lower()
        
        success = before_match and not after_match
        
        return VerificationResult(
            success=success,
            method=VerificationMethod.WINDOW_DISAPPEARED,
            evidence=f"Window {'disappeared' if success else 'still present'}",
            confidence=0.9 if success else 0.0,
        )
    
    async def _verify_process_state(
        self, 
        action: Action, 
        expected: ExpectedResult, 
        perception: PerceptionSnapshot
    ) -> VerificationResult:
        """Verify process state (running/not running)."""
        import psutil
        
        if not expected.process_name:
            return VerificationResult(
                success=False,
                method=VerificationMethod.PROCESS_STATE,
                evidence="Expected process name required",
                confidence=0.0,
            )
        
        try:
            procs = [p for p in psutil.process_iter(['name']) 
                    if expected.process_name.lower() in p.info['name'].lower()]
            running = len(procs) > 0
            
            # expected.uia_value could be "running" or "stopped"
            expected_running = expected.uia_value == "running" if expected.uia_value else True
            success = running == expected_running
            
            return VerificationResult(
                success=success,
                method=VerificationMethod.PROCESS_STATE,
                evidence=f"Process '{expected.process_name}' {'running' if running else 'not running'}",
                confidence=0.9,
                expected=expected,
                actual=ActualResult(process_name=expected.process_name),
            )
        except Exception as e:
            return VerificationResult(
                success=False,
                method=VerificationMethod.PROCESS_STATE,
                evidence=f"Process check failed: {e}",
                confidence=0.0,
            )
    
    async def _verify_clipboard(
        self, 
        action: Action, 
        expected: ExpectedResult
    ) -> VerificationResult:
        """Verify clipboard content (text, or cut/copied file paths)."""
        try:
            import win32clipboard
            clipboard_text = ""
            dropped_paths: list = []
            win32clipboard.OpenClipboard()
            try:
                try:
                    clipboard_text = win32clipboard.GetClipboardData(
                        win32clipboard.CF_UNICODETEXT) or ""
                except Exception:
                    clipboard_text = ""
                # Explorer cut/copy exposes CF_HDROP instead of text; the
                # dropped path list is the honest observable there.
                try:
                    dropped = win32clipboard.GetClipboardData(
                        win32clipboard.CF_HDROP)
                    if dropped:
                        dropped_paths = [str(p) for p in dropped]
                except Exception:
                    pass
            finally:
                win32clipboard.CloseClipboard()

            if not expected.text_expected:
                return VerificationResult(
                    success=False,
                    method=VerificationMethod.CLIPBOARD_CONTENT,
                    evidence="Expected clipboard text required",
                    confidence=0.0,
                )

            found = expected.text_expected in (clipboard_text or "")
            source = "text"
            if not found and dropped_paths:
                found = any(expected.text_expected.lower() in p.lower()
                            for p in dropped_paths)
                source = "file-drop list"
            return VerificationResult(
                success=found,
                method=VerificationMethod.CLIPBOARD_CONTENT,
                evidence=f"Clipboard {source} {'contains' if found else 'does not contain'} expected text",
                confidence=0.9 if found else 0.0,
            )
        except Exception as e:
            return VerificationResult(
                success=False,
                method=VerificationMethod.CLIPBOARD_CONTENT,
                evidence=f"Clipboard access failed: {e}",
                confidence=0.0,
            )
    
    async def _verify_screen_delta(
        self, 
        action: Action, 
        expected: ExpectedResult, 
        before: PerceptionSnapshot,
        after: PerceptionSnapshot
    ) -> VerificationResult:
        """Verify screen changed as expected."""
        if before.screenshot and after.screenshot:
            try:
                from PIL import Image
                from io import BytesIO
                import imagehash

                img1 = Image.open(BytesIO(before.screenshot))
                img2 = Image.open(BytesIO(after.screenshot))
                scope = "full screen"
                # When the action's screen location is known (a grounded
                # click), compare a crop around it: a popup menu near the
                # click dominates the crop but is noise in a fullscreen
                # hash (a real menu open measured diff 2 fullscreen).
                full_diff = (imagehash.dhash(img1.resize((32, 32)))
                             - imagehash.dhash(img2.resize((32, 32))))
                diff = full_diff
                scope = "full screen"
                # A focused crop catches local edits (selection highlight,
                # typed text) that drown in a fullscreen hash, while the
                # fullscreen hash still catches window-level change. Either
                # firing is honest evidence something moved.
                coords = getattr(action.target, 'coordinates', None)
                crop_diff = None
                if coords and img1.size == img2.size:
                    w, h = img1.size
                    cx, cy = int(coords[0]), int(coords[1])
                    side = 700
                    box = (max(0, cx - side // 2), max(0, cy - side // 2),
                           min(w, cx + side // 2), min(h, cy + side // 2))
                    if box[2] - box[0] > 64 and box[3] - box[1] > 64:
                        c1 = img1.crop(box)
                        c2 = img2.crop(box)
                        crop_diff = (imagehash.dhash(c1.resize((32, 32)))
                                     - imagehash.dhash(c2.resize((32, 32))))
                        if crop_diff > diff:
                            diff = crop_diff
                            scope = f"crop around ({cx},{cy})"

                # Expect some change but not too much
                changed = diff > 5

                return VerificationResult(
                    success=changed,
                    method=VerificationMethod.SCREEN_DELTA,
                    evidence=f"Screen perceptual hash diff: {diff} "
                             f"(full={full_diff}, crop={crop_diff}, "
                             f"threshold 5, {scope})",
                    confidence=0.7 if changed else 0.0,
                )
            except Exception as e:
                return VerificationResult(
                    success=False,
                    method=VerificationMethod.SCREEN_DELTA,
                    evidence=f"Screen delta check failed: {e}",
                    confidence=0.0,
                )
        
        return VerificationResult(
            success=False,
            method=VerificationMethod.SCREEN_DELTA,
            evidence="No screenshots available for delta comparison",
            confidence=0.0,
        )
    
    async def _verify_focused(
        self,
        action: Action,
        expected: ExpectedResult,
        perception: PerceptionSnapshot
    ) -> VerificationResult:
        """Verify keyboard focus sits in the expected control.

        This is the honest check before any keystroke step: typing goes
        to the focused control, so proving focus proves the destination.
        """
        if not expected.text_expected:
            return VerificationResult(
                success=False,
                method=VerificationMethod.FOCUSED_CONTROL,
                evidence="Expected focused control name required",
                confidence=0.0,
            )

        fc = perception.focused_control
        if fc is None or (not (fc.name or "") and not (fc.ctype or "")):
            return VerificationResult(
                success=False,
                method=VerificationMethod.FOCUSED_CONTROL,
                evidence="No focused control observed",
                confidence=0.0,
            )

        want = expected.text_expected or ""
        if want.lower().startswith("ctype:"):
            # Assert the KIND of focused control ("ctype:ListItemControl
            # |ListControl"): names vary (selected item), kinds prove
            # focus placement (file list, not an edit box).
            kinds = [k.strip().lower() for k in
                     want[len("ctype:"):].split("|")]
            found = (fc.ctype or "").lower() in kinds
        else:
            found = want.lower() in (fc.name or "").lower()
        return VerificationResult(
            success=found,
            method=VerificationMethod.FOCUSED_CONTROL,
            evidence=f"Focus is '{fc.name}' ({fc.ctype}), "
                     f"expected '{expected.text_expected}'",
            confidence=0.9 if found else 0.0,
            expected=expected,
            actual=ActualResult(uia_property="focused_control",
                                uia_value=fc.name),
        )

    async def _verify_text_read(
        self,
        action: Action,
        expected: ExpectedResult,
        actual_result: Optional[ActualResult],
    ) -> VerificationResult:
        """Verify a focus-free read produced text (and the expected text
        when the caller names it). The product under test is the read
        itself, not pixels — which is exactly what makes background reads
        verifiable while the user keeps the foreground."""
        found = ((actual_result.text_found if actual_result else None)
                 or "")
        if not found:
            return VerificationResult(
                success=False,
                method=VerificationMethod.TEXT_READ,
                evidence="Read produced no text",
                confidence=0.0,
            )
        if expected.text_expected:
            ok = expected.text_expected.lower() in found.lower()
            return VerificationResult(
                success=ok,
                method=VerificationMethod.TEXT_READ,
                evidence=f"Expected text {'found' if ok else 'missing'} "
                         f"in {len(found)}-char read",
                confidence=0.9 if ok else 0.0,
                expected=expected,
                actual=ActualResult(text_found=found[:2000]),
            )
        return VerificationResult(
            success=True,
            method=VerificationMethod.TEXT_READ,
            evidence=f"Read produced {len(found)} chars",
            confidence=0.8,
            expected=expected,
            actual=ActualResult(text_found=found[:2000]),
        )

    async def _verify_custom(
        self,
        action: Action,
        expected: ExpectedResult,
        perception: PerceptionSnapshot
    ) -> VerificationResult:
        """Custom verification - placeholder for user-defined checks."""
        if expected.custom_check:
            # Could eval a lambda or call a registered function
            pass
        
        return VerificationResult(
            success=False,
            method=VerificationMethod.CUSTOM,
            evidence="Custom verification not implemented",
            confidence=0.0,
        )
    
    def _find_control(self, target, perception: PerceptionSnapshot) -> Optional[ControlInfo]:
        """Find control matching target spec."""
        # Reuse logic from action executor
        if target.control_id:
            for ctrl in perception.controls:
                if ctrl.automation_id == target.control_id:
                    return ctrl
        
        if target.control_name:
            for ctrl in perception.controls:
                if target.control_name.lower() in ctrl.name.lower():
                    return ctrl
        
        if target.text_match:
            for region in perception.ocr_regions:
                if target.text_match.lower() in region.text.lower():
                    from .state import ControlInfo
                    return ControlInfo(
                        name=region.text,
                        x=region.x,
                        y=region.y,
                        w=region.w,
                        h=region.h,
                        ctype="ocr_text",
                    )
        
        return None