"""Full-desktop white glow pointer for contextual hand input.

This is intentionally separate from the camera preview.  It consumes only
``SpatialInteractionLayer.snapshot()["pointers"]`` and draws at the projected
Windows screen coordinates.  It is transparent to mouse/keyboard input and
never executes an action; the existing hand action bridge remains authoritative.
"""
from __future__ import annotations

import logging
import math
import time

log = logging.getLogger(__name__)

try:
    from PyQt5.QtCore import QPointF, QRect, Qt, QTimer
    from PyQt5.QtGui import QColor, QFont, QPainter, QPen
    from PyQt5.QtWidgets import QApplication, QWidget
    _QT = True
except Exception:
    _QT = False


class HandScreenPointerOverlay(QWidget):
    """Always-on-top, click-through desktop pointer overlay."""

    # Latency: the dot must track the hand, not glide behind it. 0.35s of
    # hold plus per-frame smoothing made every gesture feel laggy, so the
    # dropout hold is short and the paint position is the raw spatial point.
    POINTER_HOLD_SECONDS = 0.12
    POINTER_DEADBAND_PX = 0.0
    POINTER_SMOOTHING = 1.0
    # The overlay NEVER touches the real OS cursor. No SetCursorPos, no
    # SendInput, no synthetic mouse moves. The system pointer belongs to the
    # user. Hand actions are dispatched to the resolved target coordinates
    # instead. Kept False so the capability cannot be re-enabled by accident.
    DRIVE_OS_CURSOR = False

    def __init__(self, tracker=None, parent=None):
        if not _QT:
            raise RuntimeError("PyQt5 is required for the desktop hand pointer")
        super().__init__(parent)
        self.tracker = tracker
        self._pointers = []
        self._last_pointers = []
        self._last_pointer_at = 0.0
        self._last_raise_at = 0.0
        self._last_pointer_signature = None
        self._feedback = {}
        self._last_snapshot = {}
        self._last_os_cursor = None
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("DUDE Hand Screen Pointer")
        self._resize_to_screen()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.setInterval(33)

    def _resize_to_screen(self):
        screen = QApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        self.setGeometry(geometry)

    def attach_tracker(self, tracker) -> None:
        self.tracker = tracker
        self.refresh()

    def start(self) -> None:
        self._resize_to_screen()
        self.timer.start()
        self.show()
        self.raise_()
        self.refresh()

    def stop(self) -> None:
        self.timer.stop()
        self._pointers = []
        self._last_pointers = []
        self._last_pointer_at = 0.0
        self._last_raise_at = 0.0
        self._last_pointer_signature = None
        self._feedback = {}
        self.hide()
        self.update()

    def _sync_os_cursor(self, pointers) -> None:
        """Deliberately does nothing.

        The real Windows cursor belongs to the user. This overlay only PAINTS
        one dot per detected hand; it never injects mouse input, never calls
        SetCursorPos or SendInput, and never moves the system pointer. Hand
        actions reach applications by dispatching a click at the resolved
        screen coordinates, not by hijacking the physical cursor.
        """
        return

    def refresh(self) -> None:
        try:
            snapshot = self.tracker.spatial_snapshot() if self.tracker else {}
            snapshot = dict(snapshot or {})
            self._last_snapshot = dict(snapshot)
            raw_by_hand = {}
            for item in (snapshot.get("pointers") or []):
                if item.get("screen_xy") is None:
                    continue
                # The spatial layer emits one active pointer per hand.  Keep
                # this defensive de-duplication so a malformed snapshot can
                # never turn into multiple desktop cursors.
                raw_by_hand[str(item.get("hand_id", ""))] = dict(item)
            raw_pointers = list(raw_by_hand.values())
            now = time.monotonic()
            spatial_grabbed = any(
                bool(hand.get("grabbed"))
                for hand in (snapshot.get("hands") or ())
                if isinstance(hand, dict)
            )
            if spatial_grabbed:
                self._last_pointers = []
                self._last_pointer_at = 0.0
                pointers = []
            else:
                previous = {
                    str(item.get("hand_id", "")): item
                    for item in self._last_pointers
                    if item.get("screen_xy") is not None
                }
                pointers = []
                for item in raw_pointers:
                    item = dict(item)
                    old = previous.get(str(item.get("hand_id", "")))
                    if old is not None:
                        try:
                            x, y = (float(value) for value in item["screen_xy"])
                            ox, oy = (float(value) for value in old["screen_xy"])
                            distance = math.hypot(x - ox, y - oy)
                            if distance <= self.POINTER_DEADBAND_PX:
                                x, y = ox, oy
                            else:
                                x = ox + (x - ox) * self.POINTER_SMOOTHING
                                y = oy + (y - oy) * self.POINTER_SMOOTHING
                            item["screen_xy"] = (x, y)
                        except (TypeError, ValueError):
                            pass
                    pointers.append(item)

                active_hands = {str(item.get("hand_id", "")) for item in raw_pointers}
                blocked_hands = {
                    str(hand.get("hand_id", ""))
                    for hand in (snapshot.get("hands") or ())
                    if isinstance(hand, dict) and hand.get("target_id")
                }
                if "hands" in snapshot:
                    for hand_id, old in previous.items():
                        if hand_id not in active_hands and hand_id not in blocked_hands:
                            pointers.append(dict(old))
                if raw_pointers:
                    self._last_pointers = [dict(item) for item in pointers]
                    self._last_pointer_at = now
                elif (self._last_pointers
                      and now - self._last_pointer_at <= self.POINTER_HOLD_SECONDS):
                    # Do not move the hold timestamp forward here.  This keeps
                    # a brief detector dropout from becoming a permanent
                    # stale pointer.
                    pointers = [
                        dict(item) for item in self._last_pointers
                        if str(item.get("hand_id", "")) not in blocked_hands
                    ]
                    if not pointers:
                        self._last_pointers = []
                        self._last_pointer_at = 0.0
                else:
                    self._last_pointers = []
                    self._last_pointer_at = 0.0
            self._pointers = pointers
            feedback = {}
            try:
                reader = getattr(self.tracker, "action_feedback", None)
                feedback = dict(reader() or {}) if callable(reader) else {}
            except Exception:
                feedback = {}
            if (feedback.get("success")
                    and len(feedback.get("screen_xy") or ()) == 2):
                try:
                    age = time.time() - float(feedback.get("timestamp", 0.0))
                    if 0.0 <= age <= 1.2:
                        self._feedback = feedback
                    else:
                        self._feedback = {}
                except Exception:
                    self._feedback = {}
            else:
                self._feedback = {}
            self._resize_to_screen()
            pointer_signature = tuple(sorted(
                (str(item.get("hand_id", "")),
                 tuple(item.get("screen_xy") or ()))
                for item in pointers))
            if pointers or self._feedback:
                if not self.isVisible():
                    self.show()
                if (pointer_signature != self._last_pointer_signature
                        or now - self._last_raise_at >= 0.25):
                    self.raise_()
                    self._last_raise_at = now
                self._last_pointer_signature = pointer_signature
            elif self.isVisible():
                # Keep the object alive but remove the visible dot when the
                # hand is targeting a spatial object or disappears.
                self.hide()
                self._last_pointer_signature = None
            self.update()
        except Exception as exc:
            log.debug("hand pointer overlay refresh failed: %s", exc)

    def paintEvent(self, _event):
        if not _QT:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            origin = self.geometry().topLeft()
            # Only ``snapshot["pointers"]`` is painted.  The full named
            # fingertip set remains available in diagnostics and the camera
            # preview, but it must not become extra desktop cursors.
            for pointer in self._pointers:
                try:
                    x, y = pointer["screen_xy"]
                    x = float(x) - origin.x()
                    y = float(y) - origin.y()
                    if not (-40 <= x <= self.width() + 40
                            and -40 <= y <= self.height() + 40):
                        continue
                    # White core with a restrained cyan/white glow.  This is
                    # the desktop pointer, not a camera-preview annotation.
                    for radius, color in (
                        (22, QColor(0, 220, 255, 24)),
                        (14, QColor(0, 220, 255, 55)),
                        (8, QColor(255, 255, 255, 210)),
                    ):
                        painter.setPen(QPen(color, 2 if radius > 8 else 1))
                        painter.setBrush(color)
                        painter.drawEllipse(QPointF(x, y), radius, radius)
                    painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
                    painter.drawEllipse(QPointF(x, y), 4, 4)
                except Exception:
                    continue
            feedback = self._feedback
            if (feedback.get("success")
                    and len(feedback.get("screen_xy") or ()) == 2):
                try:
                    x, y = feedback["screen_xy"]
                    x = float(x) - origin.x()
                    y = float(y) - origin.y()
                    verified = bool(feedback.get("verified"))
                    pulse = 26 if verified else 20
                    painter.setPen(QPen(QColor(0, 255, 120, 100), 2))
                    painter.setBrush(QColor(0, 255, 120, 55))
                    painter.drawEllipse(QPointF(x, y), pulse, pulse)
                    painter.setPen(QPen(QColor(0, 255, 120, 220), 2))
                    painter.setBrush(QColor(0, 255, 120, 210))
                    painter.drawEllipse(QPointF(x, y), 7, 7)
                except Exception:
                    pass
        finally:
            painter.end()


__all__ = ["HandScreenPointerOverlay"]
