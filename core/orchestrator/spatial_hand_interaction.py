"""Spatial hand interaction and contextual screen-pointer state.

The camera/hand tracker supplies observations.  This module owns only virtual
workspace state and semantic input events:

    hands -> spatial hit test -> direct object state OR screen pointer state

It never calls pyautogui, TaskEngine, ActionExecutor, or a permission system.
Desktop actions continue through the existing HandInteractionMapper ->
HandActionBridge -> TaskEngine path.  Virtual DUDE objects are workspace state,
not operating-system side effects, so their grab/move/scale/rotate mutations are
kept here and exposed as bounded semantic events for the existing UI/agent
 surfaces to consume.
"""
from __future__ import annotations

import collections
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .finger_pointing import (
    FINGER_TIP_LANDMARKS,
    finger_point,
    point_in_frame,
    pointing_finger,
    smooth_point,
)


@dataclass
class SpatialObject:
    """A small virtual object with a projected interaction surface."""

    object_id: str
    title: str = "DUDE Object"
    kind: str = "panel"
    x: float = 0.50
    y: float = 0.50
    z: float = 0.20
    width: float = 0.24
    height: float = 0.30
    depth: float = 0.12
    rotation: float = 0.0
    scale: float = 1.0
    color: tuple = (0, 220, 255)
    metadata: dict = field(default_factory=dict)
    controls: tuple = ()

    def bounds(self) -> tuple:
        half_w = max(0.01, self.width * max(0.05, self.scale) / 2.0)
        half_h = max(0.01, self.height * max(0.05, self.scale) / 2.0)
        return (self.x - half_w, self.y - half_h,
                self.x + half_w, self.y + half_h)

    def control_at(self, point: tuple) -> Optional[dict]:
        px, py = point
        for control in self.controls:
            try:
                name, x, y, width, height = control[:5]
                if x <= px <= x + width and y <= py <= y + height:
                    return {
                        "name": str(name),
                        "rect": (x, y, width, height),
                        "action": control[5] if len(control) > 5 else "",
                    }
            except Exception:
                continue
        return None

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id,
            "title": self.title,
            "kind": self.kind,
            "x": round(float(self.x), 5),
            "y": round(float(self.y), 5),
            "z": round(float(self.z), 5),
            "width": round(float(self.width), 5),
            "height": round(float(self.height), 5),
            "depth": round(float(self.depth), 5),
            "rotation": round(float(self.rotation), 3),
            "scale": round(float(self.scale), 4),
            "color": tuple(self.color),
            "metadata": dict(self.metadata),
            "controls": [tuple(control) for control in self.controls],
        }


@dataclass
class SpatialHandState:
    """Independent state for one physical hand in the spatial layer."""

    hand_id: str
    label: str = "unknown"
    active_finger: str = "index"
    raw_tip: tuple = (0.5, 0.5)
    previous_raw_tip: tuple = (0.5, 0.5)
    finger_points: dict = field(default_factory=dict)
    finger_screen_points: dict = field(default_factory=dict)
    tip: tuple = (0.5, 0.5)
    previous_tip: tuple = (0.5, 0.5)
    depth: float = 0.0
    velocity: tuple = (0.0, 0.0)
    gesture: str = "unknown"
    target_id: str = ""
    grabbed: bool = False
    pointer_visible: bool = False
    screen_xy: Optional[tuple] = None
    pinch_started_at: Optional[float] = None
    last_pinch_at: Optional[float] = None
    last_seen_at: Optional[float] = None
    hold_emitted: bool = False
    detached_at: Optional[float] = None
    last_event: str = ""

    def to_dict(self) -> dict:
        return {
            "hand_id": self.hand_id,
            "label": self.label,
            "active_finger": self.active_finger,
            "raw_tip": tuple(round(float(v), 5) for v in self.raw_tip),
            "fingertips": {
                name: {
                    "point": tuple(round(float(v), 5) for v in point),
                    "screen_xy": self.finger_screen_points.get(name),
                }
                for name, point in self.finger_points.items()
            },
            "tip": tuple(round(float(v), 5) for v in self.tip),
            "depth": round(float(self.depth), 5),
            "velocity": tuple(round(float(v), 5) for v in self.velocity),
            "gesture": self.gesture,
            "target_id": self.target_id,
            "grabbed": bool(self.grabbed),
            "pointer_visible": bool(self.pointer_visible),
            "screen_xy": self.screen_xy,
            "hold_emitted": bool(self.hold_emitted),
            "last_event": self.last_event,
        }


@dataclass
class _ScaleAnchor:
    """Stable two-hand reference used to translate and scale an object."""

    distance: float
    scale: float
    emitted: bool = False
    midpoint: tuple = (0.5, 0.5)


@dataclass(frozen=True)
class SpatialSemanticEvent:
    """A semantic virtual-world event; never an OS action."""

    name: str
    hand_ids: tuple
    object_id: str = ""
    point: Optional[tuple] = None
    start_point: Optional[tuple] = None
    end_point: Optional[tuple] = None
    parameters: dict = field(default_factory=dict)
    frame_id: int = 0
    timestamp: float = 0.0
    provenance: str = (
        "BareHands stage.html cursor/beginGrab/endGrab/stretched object; "
        "DUDE spatial semantic state"
    )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "hand_ids": list(self.hand_ids),
            "object_id": self.object_id,
            "point": self.point,
            "start_point": self.start_point,
            "end_point": self.end_point,
            "parameters": dict(self.parameters),
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "provenance": self.provenance,
        }


class SpatialInteractionLayer:
    """Hit-test and manipulate virtual objects without executing OS actions."""

    MAX_POINTERS = 2
    MAX_EVENTS = 64
    GRAB_RELEASE_GRACE_SECONDS = 0.45
    GRAB_DISAPPEARANCE_GRACE_SECONDS = 0.50
    PINCH_CONFIRM_DISTANCE = 0.035
    HOLD_GESTURES = frozenset({"pinch", "claw"})
    CO_GRAB_PADDING = 0.12
    SCALE_EXPANSION_GAIN = 2.0

    def __init__(self):
        self._lock = threading.RLock()
        self._objects: dict = {}
        self._hands: dict = {}
        self._events = collections.deque(maxlen=self.MAX_EVENTS)
        self._scale_origins: dict = {}
        self._last_snapshot: dict = {}

    @staticmethod
    def _display_rect() -> tuple:
        """Virtual desktop bounds, matching HandScreenMapper's frame."""
        try:
            import ctypes
            u = ctypes.windll.user32
            return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
                    u.GetSystemMetrics(78), u.GetSystemMetrics(79))
        except Exception:
            return (0, 0, 1920, 1080)

    def register_object(self, obj: SpatialObject) -> SpatialObject:
        if not isinstance(obj, SpatialObject):
            raise TypeError("register_object requires SpatialObject")
        with self._lock:
            self._objects[str(obj.object_id)] = obj
            return obj

    def add_demo_objects(self) -> tuple:
        """No-op: the demo overlay boxes were removed.

        These two floating panels ("DUDE Command Center" and "Spatial Tools")
        were placeholder targets that sat on top of the desktop. They covered
        the real applications and, because the hand pointer is judged against
        whatever is under it, they captured the gestures meant for the apps
        underneath. The spatial layer and its per-hand pointer maths are
        untouched and still work; there are simply no demo boxes competing
        with the desktop for the hand dots.
        """
        return ()

    def remove_object(self, object_id: str) -> bool:
        with self._lock:
            self._scale_origins.pop(str(object_id), None)
            return self._objects.pop(str(object_id), None) is not None

    def clear_hands(self) -> None:
        with self._lock:
            self._hands.clear()
            self._scale_origins.clear()
            self._last_snapshot = {}

    def clear(self) -> None:
        with self._lock:
            self._objects.clear()
            self._hands.clear()
            self._scale_origins.clear()
            self._last_snapshot = {}

    def object(self, object_id: str) -> Optional[SpatialObject]:
        with self._lock:
            return self._objects.get(str(object_id))

    def objects(self) -> tuple:
        with self._lock:
            return tuple(self._objects.values())

    def _hit_test(self, point: tuple, depth: float = 0.0) -> Optional[SpatialObject]:
        best = None
        best_score = float("inf")
        px, py = point
        for obj in self._objects.values():
            if not (0.0 < obj.scale <= 100.0):
                continue
            left, top, right, bottom = obj.bounds()
            if left <= px <= right and top <= py <= bottom:
                # Lower z is nearer in the virtual workspace.  The small
                # depth term makes overlapping panels deterministic.
                score = float(obj.z) + math.hypot(px - obj.x, py - obj.y)
                if score < best_score:
                    best, best_score = obj, score
        return best

    @staticmethod
    def _identity(hand, index: int) -> str:
        value = str(getattr(hand, "identity", "") or
                    getattr(hand, "label", "") or f"hand_{index}").lower()
        return value or f"hand_{index}"

    @staticmethod
    def _tip(hand, gesture: str = "", previous: str = "index") -> tuple:
        try:
            finger = (pointing_finger(hand, previous)
                      if gesture == "pointing" else "index")
            point = finger_point(hand, finger)
            if point is not None:
                return point
        except Exception:
            pass
        try:
            point = getattr(hand, "palm", ()) or getattr(hand, "wrist", ())
            return (float(point[0]), float(point[1]))
        except Exception:
            return (0.5, 0.5)

    @staticmethod
    def _depth(hand) -> float:
        try:
            lm = getattr(hand, "landmarks_3d", ()) or ()
            if len(lm) > 8 and len(lm[8]) > 2:
                return float(lm[8][2])
        except Exception:
            pass
        return 0.0

    def _emit(self, event: SpatialSemanticEvent) -> None:
        self._events.append(event)

    def _release_hand(self, hand_id: str, now: float,
                      frame_id: int = 0) -> None:
        state = self._hands.get(hand_id)
        if state is None or not state.grabbed:
            return
        obj = self._objects.get(state.target_id)
        state.grabbed = False
        state.pinch_started_at = None
        state.last_pinch_at = None
        state.hold_emitted = False
        state.detached_at = None
        state.last_event = "release"
        self._emit(SpatialSemanticEvent(
            "release", (hand_id,), state.target_id, state.tip,
            parameters={"x": obj.x, "y": obj.y,
                        "scale": obj.scale, "rotation": obj.rotation},
            frame_id=frame_id, timestamp=now))

    def update(self, state, stable_by_hand: Optional[dict] = None,
               screen_mapper=None, now: Optional[float] = None) -> dict:
        """Update both hands and return the current contextual pointer state."""
        timestamp = float(time.time() if now is None else now)
        frame_id = int(getattr(state, "frame_id", 0) or 0)
        stable_by_hand = stable_by_hand or {}
        incoming = list(getattr(state, "hands", ()) or ())[:2]
        current_ids = set()
        with self._lock:
            for index, hand in enumerate(incoming):
                hand_id = self._identity(hand, index)
                current_ids.add(hand_id)
                old = self._hands.get(hand_id)
                new_hand = old is None
                if old is None:
                    old = SpatialHandState(hand_id, str(
                        getattr(hand, "label", "unknown")))
                    self._hands[hand_id] = old
                rejoined = old.detached_at is not None
                old.previous_tip = old.tip
                old.previous_raw_tip = old.raw_tip
                old.depth = self._depth(hand)
                stable = stable_by_hand.get(hand_id,
                                             (getattr(hand, "gesture", None), 0))
                raw_gesture = getattr(stable[0], "value", stable[0])
                old.gesture = str(raw_gesture or "unknown").lower()
                # The stabilizer may still report PINCH for a few frames
                # after the fingers have separated. Validate the observed
                # thumb/index distance before allowing that stale vote to
                # keep a virtual object attached.
                if old.gesture == "pinch":
                    try:
                        pinch_distance = getattr(hand, "pinch_distance", None)
                        if (pinch_distance is not None
                                and float(pinch_distance)
                                > self.PINCH_CONFIRM_DISTANCE):
                            observed = getattr(hand, "gesture", None)
                            observed = str(
                                getattr(observed, "value", observed)
                                or "open").lower()
                            old.gesture = (observed
                                           if observed not in {
                                               "", "unknown", "pinch"
                                           } else "open")
                    except (TypeError, ValueError):
                        pass
                if old.gesture == "pointing":
                    old.active_finger = pointing_finger(
                        hand, old.active_finger)
                old.raw_tip = self._tip(
                    hand, old.gesture, old.active_finger)
                old.tip = (old.raw_tip if new_hand
                           else smooth_point(old.tip, old.raw_tip,
                                             alpha=0.85, deadband=0.0))
                finger_points = {}
                finger_screen_points = {}
                for finger_name, landmark_index in FINGER_TIP_LANDMARKS.items():
                    try:
                        raw_point = hand.landmarks[landmark_index]
                    except Exception:
                        continue
                    if not point_in_frame(raw_point):
                        continue
                    finger_point = (raw_point if new_hand else smooth_point(
                        old.finger_points.get(finger_name), raw_point,
                        alpha=0.85, deadband=0.0))
                    finger_points[finger_name] = finger_point
                    if screen_mapper is not None:
                        try:
                            finger_screen_points[finger_name] = tuple(
                                screen_mapper.map(finger_point[0], finger_point[1]))
                        except Exception:
                            pass
                old.finger_points = finger_points
                old.finger_screen_points = finger_screen_points
                if old.active_finger in finger_points:
                    old.tip = finger_points[old.active_finger]
                old.velocity = (old.tip[0] - old.previous_tip[0],
                                old.tip[1] - old.previous_tip[1])
                old.label = str(getattr(hand, "label", old.label)).lower()
                old.last_seen_at = timestamp

                # A single unstable frame must not cut a live grab. Keep
                # the object attached through a short pinch dropout, then
                # require a sustained non-pinch pose before releasing.
                if old.grabbed and old.gesture not in self.HOLD_GESTURES:
                    if (old.last_pinch_at is not None
                            and timestamp - old.last_pinch_at
                            <= self.GRAB_RELEASE_GRACE_SECONDS):
                        old.last_event = "grab_hold"
                        old.pointer_visible = False
                        old.screen_xy = None
                        continue
                    self._release_hand(hand_id, timestamp, frame_id)
                old.detached_at = None

                hit = self._hit_test(old.tip, old.depth)
                if hit is None and old.gesture == "pinch":
                    # Once one hand owns an object, let a second hand join
                    # the same object within a small spatial tolerance.  The
                    # hand remains an independent grabber; this only avoids
                    # requiring both fingertips to land on the exact same
                    # animated rectangle before scaling can begin.
                    for candidate in self._hands.values():
                        if (candidate.hand_id == hand_id
                                or not candidate.grabbed):
                            continue
                        target = self._objects.get(candidate.target_id)
                        if target is None:
                            continue
                        left, top, right, bottom = target.bounds()
                        if (left - self.CO_GRAB_PADDING <= old.tip[0]
                                <= right + self.CO_GRAB_PADDING
                                and top - self.CO_GRAB_PADDING <= old.tip[1]
                                <= bottom + self.CO_GRAB_PADDING):
                            hit = target
                            break
                if old.grabbed:
                    if old.gesture in self.HOLD_GESTURES:
                        old.last_pinch_at = timestamp
                    obj = self._objects.get(old.target_id)
                    if obj is None:
                        old.grabbed = False
                        old.target_id = ""
                    else:
                        other = [candidate for candidate in self._hands.values()
                                 if candidate.hand_id != hand_id
                                 and candidate.grabbed
                                 and candidate.gesture == "pinch"
                                 and candidate.target_id == obj.object_id
                                 and candidate.last_seen_at is not None
                                 and timestamp - candidate.last_seen_at
                                 <= self.GRAB_DISAPPEARANCE_GRACE_SECONDS]
                        if other and old.gesture == "pinch":
                            # BareHands two-hand stretch: opposite sides of
                            # one object define a scale baseline.
                            pair = (other[0], old)
                            distance = math.hypot(
                                pair[0].raw_tip[0] - pair[1].raw_tip[0],
                                pair[0].raw_tip[1] - pair[1].raw_tip[1])
                            origin = self._scale_origins.get(obj.object_id)
                            midpoint = (
                                (pair[0].raw_tip[0] + pair[1].raw_tip[0]) / 2.0,
                                (pair[0].raw_tip[1] + pair[1].raw_tip[1]) / 2.0,
                            )
                            if origin is None:
                                origin = _ScaleAnchor(
                                    distance=max(distance, 0.001),
                                    scale=obj.scale,
                                    midpoint=midpoint,
                                )
                                self._scale_origins[obj.object_id] = origin
                            # Keep the object centered between the two actual
                            # grab points while scaling. This prevents the
                            # rectangle from visually sliding out from under
                            # a user's pinch. If a hand briefly disappeared,
                            # rebase the midpoint rather than applying the
                            # whole missing interval as a translation jump.
                            if rejoined:
                                origin.midpoint = midpoint
                            else:
                                obj.x += midpoint[0] - origin.midpoint[0]
                                obj.y += midpoint[1] - origin.midpoint[1]
                                origin.midpoint = midpoint
                            raw_ratio = distance / max(origin.distance, 0.001)
                            # Expansion is intentionally more responsive
                            # than contraction: a real two-hand stretch
                            # should produce a visible size change without
                            # making the object collapse on small jitter.
                            gain = (self.SCALE_EXPANSION_GAIN
                                    if raw_ratio >= 1.0 else 1.0)
                            ratio = max(0.12, min(12.0,
                                                  1.0 + (raw_ratio - 1.0) * gain))
                            obj.scale = max(0.12, min(12.0,
                                                       origin.scale * ratio))
                            if abs(distance - origin.distance) >= 0.015 \
                                    and not origin.emitted:
                                origin.emitted = True
                                self._emit(SpatialSemanticEvent(
                                    "scale", tuple(sorted(
                                        (pair[0].hand_id, pair[1].hand_id))),
                                    obj.object_id, old.tip,
                                    parameters={"scale": obj.scale,
                                                "distance": distance},
                                    frame_id=frame_id, timestamp=timestamp))
                            old.last_event = "scale"
                        else:
                            dx = old.raw_tip[0] - old.previous_raw_tip[0]
                            dy = old.raw_tip[1] - old.previous_raw_tip[1]
                            obj.x += dx
                            obj.y += dy
                            held = (timestamp - old.pinch_started_at
                                    if old.pinch_started_at else 0.0)
                            if held >= 0.8 and (abs(dx) + abs(dy)) >= 0.01:
                                obj.rotation += (dx - dy) * 90.0
                                old.last_event = "rotate"
                                self._emit(SpatialSemanticEvent(
                                    "rotate", (hand_id,), obj.object_id,
                                    old.tip, old.previous_tip, old.tip,
                                    {"rotation": obj.rotation},
                                    frame_id, timestamp))
                            elif abs(dx) + abs(dy) >= 0.002:
                                old.last_event = "move"
                                self._emit(SpatialSemanticEvent(
                                    "move", (hand_id,), obj.object_id,
                                    old.tip, old.previous_tip, old.tip,
                                    {"x": obj.x, "y": obj.y},
                                    frame_id, timestamp))
                elif hit is not None:
                    old.target_id = hit.object_id
                    old.pointer_visible = False
                    old.screen_xy = None
                    if old.gesture in self.HOLD_GESTURES:
                        old.grabbed = True
                        old.pinch_started_at = timestamp
                        old.last_pinch_at = timestamp
                        old.last_event = "grab"
                        self._emit(SpatialSemanticEvent(
                            "touch", (hand_id,), hit.object_id, old.tip,
                            frame_id=frame_id, timestamp=timestamp))
                        control = hit.control_at(old.tip)
                        if control is not None:
                            self._emit(SpatialSemanticEvent(
                                "control_touch", (hand_id,), hit.object_id,
                                old.tip, parameters=control,
                                frame_id=frame_id, timestamp=timestamp))
                        self._emit(SpatialSemanticEvent(
                            "grab", (hand_id,), hit.object_id, old.tip,
                            frame_id=frame_id, timestamp=timestamp))
                        co_grabbers = (
                            [
                                candidate for candidate in self._hands.values()
                                if candidate.hand_id != hand_id
                                and candidate.grabbed
                                and candidate.gesture == "pinch"
                                and candidate.target_id == hit.object_id
                            ] if old.gesture == "pinch" else []
                        )
                        if co_grabbers:
                            distance = math.hypot(
                                old.raw_tip[0] - co_grabbers[0].raw_tip[0],
                                old.raw_tip[1] - co_grabbers[0].raw_tip[1])
                            self._scale_origins[hit.object_id] = _ScaleAnchor(
                                distance=max(distance, 0.001),
                                scale=hit.scale,
                                midpoint=(
                                    (old.raw_tip[0]
                                     + co_grabbers[0].raw_tip[0]) / 2.0,
                                    (old.raw_tip[1]
                                     + co_grabbers[0].raw_tip[1]) / 2.0,
                                ),
                            )
                else:
                    old.target_id = ""
                    old.last_event = "screen_pointer"
                    old.pointer_visible = (
                        old.active_finger in old.finger_screen_points)
                    old.screen_xy = old.finger_screen_points.get(
                        old.active_finger)

            for hand_id in list(self._hands):
                if hand_id in current_ids:
                    continue
                missing = self._hands.get(hand_id)
                if (missing is not None
                        and missing.grabbed
                        and missing.last_seen_at is not None
                        and timestamp - missing.last_seen_at
                        <= self.GRAB_DISAPPEARANCE_GRACE_SECONDS):
                    # A detector dropout must not cut an active connection.
                    # Keep the state reserved briefly so the same identity can
                    # reappear and continue the same grab.
                    if missing.detached_at is None:
                        missing.detached_at = timestamp
                    missing.pointer_visible = False
                    missing.screen_xy = None
                    missing.last_event = "grab_hold"
                    continue
                self._release_hand(hand_id, timestamp, frame_id)
                self._hands.pop(hand_id, None)

            snapshot = self._snapshot_locked()
            self._last_snapshot = snapshot
            return dict(snapshot)

    def _snapshot_locked(self) -> dict:
        pointers = []
        fingertip_pointers = []
        for hand in self._hands.values():
            for finger_name, screen_xy in hand.finger_screen_points.items():
                fingertip_pointers.append({
                    "hand_id": hand.hand_id,
                    "finger": finger_name,
                    "label": hand.label,
                    "screen_xy": screen_xy,
                    "active": finger_name == hand.active_finger,
                })
            if hand.pointer_visible:
                pointers.append({
                    "hand_id": hand.hand_id,
                    "label": hand.label,
                    "tip": tuple(hand.tip),
                    "screen_xy": hand.screen_xy,
                    "world": "screen",
                })
        has_spatial = any(h.grabbed or h.target_id
                          for h in self._hands.values())
        if pointers and has_spatial:
            world = "mixed"
        elif pointers:
            world = "screen"
        elif has_spatial:
            world = "spatial"
        else:
            world = "idle"
        return {
            "world": world,
            "pointers": pointers,
            "fingertips": fingertip_pointers,
            "hands": [hand.to_dict() for hand in self._hands.values()],
            "objects": [obj.to_dict() for obj in self._objects.values()],
            "event_count": len(self._events),
        }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._last_snapshot or self._snapshot_locked())

    def events(self, limit: int = 16) -> list:
        with self._lock:
            items = list(self._events)[-max(1, int(limit)):]
            return [event.to_dict() for event in items]

    def clear_events(self) -> list:
        with self._lock:
            result = [event.to_dict() for event in self._events]
            self._events.clear()
            return result

    def paint(self, painter, width: int, height: int) -> None:
        """Paint virtual surfaces and only screen-world pointers."""
        try:
            from PyQt5.QtCore import QPointF, QRectF, Qt
            from PyQt5.QtGui import QColor, QFont, QPen
        except Exception:
            return
        try:
            with self._lock:
                objects = list(self._objects.values())
                hands = list(self._hands.values())
            painter.save()
            try:
                for obj in objects:
                    left, top, right, bottom = obj.bounds()
                    x = left * width
                    y = top * height
                    w = max(8.0, (right - left) * width)
                    h = max(8.0, (bottom - top) * height)
                    depth = max(2.0, min(16.0, obj.z * width * 0.18))
                    rect = QRectF(x + depth, y + depth, w, h)
                    color = QColor(*obj.color)
                    color.setAlpha(42)
                    painter.setPen(QPen(QColor(*obj.color), 2))
                    painter.setBrush(color)
                    painter.drawRoundedRect(rect, 12, 12)
                    painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
                    painter.drawLine(int(x + depth), int(y + depth),
                                     int(x + depth), int(y + depth + h))
                    painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
                    painter.setPen(QColor(*obj.color))
                    painter.drawText(int(x + depth + 10),
                                     int(y + depth + 18), obj.title[:28])
                    for hand in hands:
                        if hand.target_id == obj.object_id:
                            painter.setPen(QPen(QColor(255, 255, 255, 180), 2))
                            painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3),
                                                    14, 14)
                for pointer in self.snapshot().get("pointers", []):
                    # Draw from the SCREEN point, not the raw camera tip.
                    # The camera tip is un-mirrored while every screen-space
                    # consumer (desktop dot, click dispatch) is mirrored, so
                    # plotting the raw tip here made this marker travel the
                    # opposite way to the hand and to the real cursor.
                    sxy = pointer.get("screen_xy")
                    if sxy is not None and len(sxy) == 2:
                        vx, vy, vw, vh = self._display_rect()
                        tx = (float(sxy[0]) - vx) / max(1, vw)
                        ty = (float(sxy[1]) - vy) / max(1, vh)
                    else:
                        tx, ty = pointer["tip"]
                    px = max(3.0, min(width - 3.0, tx * width))
                    py = max(3.0, min(height - 3.0, ty * height))
                    painter.setPen(QPen(QColor(255, 255, 255, 220), 5))
                    painter.drawEllipse(QPointF(px, py), 5, 5)
                    painter.setPen(QPen(QColor(0, 220, 255, 150), 10))
                    painter.drawEllipse(QPointF(px, py), 9, 9)
                    painter.setFont(QFont("Segoe UI", 7))
                    painter.setPen(QColor(255, 255, 255, 200))
                    painter.drawText(int(px + 12), int(py - 8),
                                     "SCREEN " + pointer["hand_id"])
            finally:
                painter.restore()
        except Exception:
            return


__all__ = [
    "SpatialObject",
    "SpatialHandState",
    "SpatialSemanticEvent",
    "SpatialInteractionLayer",
]
