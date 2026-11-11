"""Phase 16: hand/gesture interaction channel (sensor, not a brain).

Pipeline (strict order):

    CAMERA -> HandCamera -> HandPerception -> HandStateBus -+
                                                            +-> HandTracker
    VisualStateBus -----------------------------------------+-> relate()

The tracker publishes SENSOR OBSERVATIONS (gesture + confidence +
freshness) and bounded BareHands-derived semantic proposals. A proposal is
queued only as the existing Action/TaskEngine representation; it never
executes directly and therefore still passes permission, action-fence,
ActionExecutor, verification, and recovery authorities.

Bounds everywhere: single-slot frame buffer, single-slot state bus,
5-vote stabilizer, at most 2 hands. No video archive, no frame
history, no cloud. The landmark model is a local file; when it or the
camera is missing the capability reports DEGRADED/UNAVAILABLE and the
rest of DUDE is untouched.
"""
from __future__ import annotations

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Optional

from .finger_pointing import (
    FINGER_PIP_LANDMARKS,
    FINGER_TIP_LANDMARKS,
    finger_point,
    pointing_finger,
    smooth_point,
)
from .spatial_hand_interaction import SpatialInteractionLayer

log = logging.getLogger(__name__)

MODEL_PATH = r"E:\Dude\dude\dude_data\models\hand_landmarker.task"
MAX_HANDS = 2
STABLE_VOTES = 5
STABLE_NEED = 3
HAND_TTL_SECONDS = 1.0
# Signal-quality gate on the handedness score (see HandObservation).
# The Tasks detector applies its own presence threshold internally and
# does NOT expose a per-hand detector score; this gate only measures
# how clean the observed hand features are.
MIN_SIGNAL_SCORE = 0.5
MIN_GESTURE_CONF = 0.6
# Bounded camera-open isolation (Windows/DirectShow opens on dead
# indices can block for minutes): the open attempt runs on a daemon
# worker and the caller never waits longer than this.
OPEN_TIMEOUT_SECONDS = 8.0

# One DUDE process owns at most one capture handle per physical index. This
# is an internal ownership guard, not an exclusive hardware lock: the chosen
# Windows Media Foundation backend still requests the platform's shared path.
_CAMERA_OWNER_LOCK = threading.Lock()
_ACTIVE_CAMERA_OWNERS: dict = {}
_CAMERA_SOURCES: dict = {}

class Gesture(Enum):
    UNKNOWN = "unknown"
    OPEN = "open"
    CLOSED = "closed"
    POINTING = "pointing"
    PINCH = "pinch"
    CLAW = "claw"
    THUMBS_UP = "thumbs_up"
    RAISED = "raised"
    THREE_FINGER = "three_finger"


class SemanticInteraction(Enum):
    """Bounded hand meanings that may cross into TaskEngine."""

    NONE = "none"
    POINTER = "pointer"
    TAP = "tap"
    CLAW = "claw"
    DRAG = "drag"
    HOLD = "hold"
    TWO_HAND_SCALE = "two_hand_scale"
    FLICK = "flick"
    CLAP = "clap"
    WINDOW_MINIMIZE = "window_minimize"


def _d(a, b) -> float:
    try:
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    except Exception:
        return 0.0


def classify_gesture(lm) -> tuple:
    """Pure landmark geometry -> (Gesture, confidence).

    lm: 21 (x, y) normalized points (MediaPipe ordering). Rotation
    tolerant (wrist-relative distances, not absolute up). Never raises.
    """
    try:
        if not lm or len(lm) < 21:
            return Gesture.UNKNOWN, 0.0
        wrist = lm[0]
        fingers = {
            "index": (lm[8], lm[6], lm[5]),
            "middle": (lm[12], lm[10], lm[9]),
            "ring": (lm[16], lm[14], lm[13]),
            "pinky": (lm[20], lm[18], lm[17]),
        }
        extended = {}
        finger_ratios = {}
        for name, (tip, pip, mcp) in fingers.items():
            try:
                ratio = _d(tip, wrist) / max(_d(pip, wrist), 0.001)
                finger_ratios[name] = ratio
                extended[name] = ratio > 1.15
            except Exception:
                finger_ratios[name] = 0.0
                extended[name] = False
        n_ext = sum(1 for v in extended.values() if v)
        try:
            thumb_out = _d(lm[4], lm[17]) > _d(lm[3], lm[17]) * 1.1
        except Exception:
            thumb_out = False
        if (finger_ratios.get("index", 0.0) > 1.18
                and finger_ratios.get("middle", 0.0) > 1.18
                and finger_ratios.get("ring", 0.0) > 1.18
                and finger_ratios.get("pinky", 0.0) < 1.15):
            return Gesture.THREE_FINGER, 0.82

        # Pinch: thumb tip near index tip (checked first — it can look
        # like almost-pointing). The threshold is relative to hand scale so
        # a normal webcam view does not require an artificially tight pinch.
        try:
            palm_scale = max(
                _d(wrist, lm[9]), _d(lm[5], lm[17]), 0.05)
            # Enter pinch only when the tips are genuinely close. A loose
            # near-pinch is still POINTING, not a click gesture.
            pinch_threshold = max(0.018, min(0.035,
                                             palm_scale * 0.16))
            if _d(lm[4], lm[8]) < pinch_threshold:
                return Gesture.PINCH, 0.85
        except Exception:
            pass

        # BareHands-derived claw: the middle/ring fingers are curled while
        # the pinky remains out and the thumb/index do not form a pinch.
        # This stays distinct from a fist and from a point.
        if (not extended.get("index") and not extended.get("middle")
                and not extended.get("ring") and extended.get("pinky")
                and not thumb_out):
            return Gesture.CLAW, 0.78

        if n_ext == 0 and not thumb_out:
            return Gesture.CLOSED, 0.85
        if n_ext == 4:
            try:
                if wrist[1] < 0.40:
                    return Gesture.RAISED, 0.75
            except Exception:
                pass
            return Gesture.OPEN, 0.9 if thumb_out else 0.75
        if n_ext == 1:
            # Any single extended finger is a pointing/pointer pose.  The
            # mapper selects that finger's tip for the screen cursor, so
            # index/middle/ring/pinky can all control the same interaction.
            return Gesture.POINTING, 0.85
        try:
            if (thumb_out and not extended.get("index")
                    and not extended.get("middle")
                    and not extended.get("ring")
                    and not extended.get("pinky")
                    and lm[4][1] < lm[3][1] - 0.05):
                return Gesture.THUMBS_UP, 0.8
        except Exception:
            pass
        return Gesture.UNKNOWN, 0.3 if n_ext else 0.2
    except Exception as e:
        log.warning(f"classify_gesture failed: {e}")
        return Gesture.UNKNOWN, 0.0


@dataclass
class HandObservation:
    """One hand: normalized geometry + sensor classification.

    handedness_score is the on-device handedness (left/right)
    classifier score (0..1) — the ONLY per-hand numeric score the
    MediaPipe Tasks HandLandmarker API exposes (hand_landmarks,
    hand_world_landmarks, handedness; no detector/presence score).
    It is used purely as a signal-quality gate: crisp hand features
    score high, blurry/partial hands score low. It is NOT detector
    confidence — the detector's presence threshold applies internally
    (min_hand_detection_confidence) and is unobservable per hand.
    """
    label: str = "unknown"  # left | right | unknown
    identity: str = ""  # stable key for per-hand temporal state
    wrist: tuple = ()
    palm: tuple = ()
    bbox: tuple = ()  # (x, y, w, h) normalized 0..1
    landmarks: tuple = ()  # 21 (x, y) normalized
    landmarks_3d: tuple = ()  # 21 (x, y, z) when the model exposes depth
    handedness_score: float = 0.0
    gesture: Gesture = Gesture.UNKNOWN
    gesture_confidence: float = 0.0
    captured_at: float = field(default_factory=time.time)

    @property
    def fingertips(self) -> dict:
        """Named 2-D tips for all five fingers, including the thumb."""
        points = getattr(self, "landmarks", ()) or ()
        return {
            name: tuple(points[index])
            for name, index in FINGER_TIP_LANDMARKS.items()
            if len(points) > index and len(points[index]) >= 2
        }

    @property
    def fingertips_3d(self) -> dict:
        """Named x/y/z tips when depth landmarks are available."""
        points = getattr(self, "landmarks_3d", ()) or ()
        return {
            name: tuple(points[index])
            for name, index in FINGER_TIP_LANDMARKS.items()
            if len(points) > index and len(points[index]) >= 3
        }

    @property
    def pinch_distance(self) -> Optional[float]:
        points = getattr(self, "landmarks", ()) or ()
        if len(points) > 8 and len(points[4]) >= 2 and len(points[8]) >= 2:
            return _d(points[4], points[8])
        return None


@dataclass
class HandState:
    """Canonical in-memory hand state (bounded, no frames)."""
    hands: tuple = ()
    captured_at: float = field(default_factory=time.time)
    source: str = ""
    frame_id: int = 0

    def count(self) -> int:
        try:
            return len(self.hands)
        except Exception:
            return 0

    def primary(self) -> Optional[HandObservation]:
        try:
            if not self.hands:
                return None
            return max(self.hands,
                       key=lambda h: getattr(h, "handedness_score",
                                             0.0))
        except Exception:
            return None

    def by_label(self, label: str) -> Optional[HandObservation]:
        """Return one hand without collapsing the other hand's state."""
        wanted = str(label or "").strip().lower()
        try:
            for hand in self.hands:
                if str(getattr(hand, "label", "")).lower() == wanted:
                    return hand
        except Exception:
            pass
        return None

    @property
    def left(self) -> Optional[HandObservation]:
        return self.by_label("left")

    @property
    def right(self) -> Optional[HandObservation]:
        return self.by_label("right")

    def identities(self) -> tuple:
        try:
            return tuple(
                str(getattr(hand, "identity", "") or
                    getattr(hand, "label", "unknown"))
                for hand in self.hands)
        except Exception:
            return ()

    def fingertips_by_hand(self) -> dict:
        """Return all named fingertips for every currently detected hand."""
        return {
            str(getattr(hand, "identity", "") or
                getattr(hand, "label", "unknown")): hand.fingertips
            for hand in (self.hands or ())
        }

    def two_hands(self) -> bool:
        return self.count() >= 2

    def age_seconds(self) -> float:
        try:
            return max(0.0, time.time() - self.captured_at)
        except Exception:
            return 0.0

    def is_stale(self, ttl_seconds: float = HAND_TTL_SECONDS) -> bool:
        return self.age_seconds() > ttl_seconds


class HandStabilizer:
    """Bounded vote window: N-of-last-M agreement becomes stable."""

    def __init__(self, votes: int = STABLE_VOTES,
                 need: int = STABLE_NEED):
        self._votes: collections.deque = collections.deque(maxlen=votes)
        self._need = need
        self._lock = threading.Lock()

    def update(self, gesture: Gesture) -> tuple:
        """Feed one frame's gesture; returns (stable, votes_for_it)."""
        try:
            with self._lock:
                self._votes.append(gesture)
                if len(self._votes) < self._need:
                    return Gesture.UNKNOWN, 0
                counts: dict = {}
                for g in self._votes:
                    counts[g] = counts.get(g, 0) + 1
                best = max(counts.items(), key=lambda kv: kv[1])
                if best[0] == Gesture.UNKNOWN or best[1] < self._need:
                    return Gesture.UNKNOWN, best[1]
                return best[0], best[1]
        except Exception:
            return Gesture.UNKNOWN, 0

    def reset(self) -> None:
        try:
            with self._lock:
                self._votes.clear()
        except Exception:
            pass


class HandIdentityTracker:
    """Keep BareHands-style per-cursor identity across detector reordering.

    MediaPipe's handedness label is a useful observation, but it is not a
    stable identity: the detector may reorder hands and its left/right label
    can flip with mirroring/occlusion.  BareHands keys its temporal cursor
    state by the per-frame hand slot.  DUDE keeps that useful property while
    matching slots by palm distance, so one physical hand keeps one temporal
    state even when the detector returns it in another order.
    """

    def __init__(self, max_gap_seconds: float = 1.5):
        self.max_gap_seconds = float(max_gap_seconds)
        self._slots: dict = {}
        self._lock = threading.RLock()

    @staticmethod
    def _point(hand: HandObservation) -> Optional[tuple]:
        try:
            point = getattr(hand, "palm", ()) or getattr(hand, "wrist", ())
            if point and len(point) >= 2:
                return (float(point[0]), float(point[1]))
        except Exception:
            pass
        return None

    def assign(self, hands) -> tuple:
        """Return observations with stable ``hand_N`` identities assigned."""
        result = []
        now = time.time()
        with self._lock:
            candidates = list(hands or ())[:MAX_HANDS]
            available = set(self._slots)
            assigned = set()
            preferred_id = ""
            if len(candidates) == 1 and available:
                fresh = [
                    identity for identity in available
                    if now - float(self._slots[identity].get("seen_at", 0.0))
                    <= self.max_gap_seconds
                ]
                if fresh:
                    label = str(getattr(candidates[0], "label", "")).lower()
                    same_label = [
                        identity for identity in fresh
                        if str(self._slots[identity].get("label", "")).lower()
                        == label
                    ]
                    preferred_id = max(
                        same_label or fresh,
                        key=lambda identity: float(self._slots[identity].get(
                            "seen_at", 0.0)))
            for hand in candidates:
                point = self._point(hand)
                best_id = preferred_id if preferred_id and not assigned else ""
                best_cost = 0.0 if best_id else float("inf")
                for identity in available - assigned:
                    slot = self._slots[identity]
                    if now - float(slot.get("seen_at", 0.0)) > self.max_gap_seconds:
                        continue
                    old_point = slot.get("point")
                    distance = _d(point, old_point) if point and old_point else 1.0
                    # Spatial continuity is authoritative.  Handedness is a
                    # small tie-breaker only; it must not swap identities by
                    # itself when the two labels are noisy.
                    old_label = str(slot.get("label", "")).lower()
                    new_label = str(getattr(hand, "label", "")).lower()
                    label_penalty = 0.0 if not old_label or not new_label \
                        or old_label == new_label else 0.25
                    cost = distance + label_penalty
                    if cost < best_cost:
                        best_id, best_cost = identity, cost
                if not best_id or best_cost > 0.80:
                    # Reuse the fixed two-slot namespace.  Never expose an
                    # ever-growing hand_5/hand_6 identity after a brief
                    # detector dropout.
                    free = [f"hand_{index}" for index in range(MAX_HANDS)
                            if f"hand_{index}" not in self._slots
                            and f"hand_{index}" not in assigned]
                    if free:
                        best_id = free[0]
                    else:
                        fallback = [
                            identity for identity in available - assigned
                            if str(self._slots[identity].get("label", "")).lower()
                            == str(getattr(hand, "label", "")).lower()
                        ]
                        best_id = (fallback or list(available - assigned))[0]
                assigned.add(best_id)
                self._slots[best_id] = {
                    "point": point,
                    "label": str(getattr(hand, "label", "")).lower(),
                    "seen_at": now,
                }
                try:
                    hand.identity = best_id
                except Exception:
                    pass
                result.append(hand)
            # Missing slots remain available briefly for re-acquisition, but
            # they are never emitted as active hands and cannot become stale
            # second-hand state.
            for identity, slot in list(self._slots.items()):
                if now - float(slot.get("seen_at", 0.0)) > self.max_gap_seconds:
                    self._slots.pop(identity, None)
        return tuple(result)


class BareHandsCursorState:
    """Per-hand temporal state adapted from BareHands' cursor objects.

    This is deliberately a sensor/interpreter state, not an executor.  It
    carries the bounded pieces of BareHands' working implementation that
    matter to DUDE: thumb/index cursor history, pinch hysteresis, open-hand
    timestamps, vertical-palm evidence, two-hand scale distance, and the
    open-to-claw transition gate.
    """

    def __init__(self, identity: str):
        self.identity = str(identity)
        self.history = collections.deque(maxlen=10)
        self.last_point: Optional[tuple] = None
        self.pinched = False
        self.pinch_started_at: Optional[float] = None
        self.pinch_start_point: Optional[tuple] = None
        self.pinch_pointer_start_point: Optional[tuple] = None
        self.press_emitted = False
        self.pinch_clicked = False
        self.last_release: Optional[dict] = None
        self.last_pinch_at: Optional[float] = None
        self.last_open_at: Optional[float] = None
        self.last_soft_open_at: Optional[float] = None
        self.hand_up = False
        self.claw_streak = 0
        self.claw_armed = False
        self.claw_emitted = False
        self.claw_lost_at: Optional[float] = None
        self.hold_emitted = False
        self.pointing_started_at: Optional[float] = None
        self.pointing_start_point: Optional[tuple] = None
        self.pointing_emitted = False
        self.last_point_release: Optional[dict] = None
        self.last_seen_at: Optional[float] = None

    @staticmethod
    def point_for(hand: HandObservation) -> Optional[tuple]:
        try:
            lm = getattr(hand, "landmarks", ()) or ()
            if len(lm) > 8 and len(lm[4]) >= 2 and len(lm[8]) >= 2:
                return ((float(lm[4][0]) + float(lm[8][0])) / 2.0,
                        (float(lm[4][1]) + float(lm[8][1])) / 2.0)
        except Exception:
            pass
        try:
            point = getattr(hand, "palm", ()) or getattr(hand, "wrist", ())
            if point and len(point) >= 2:
                return (float(point[0]), float(point[1]))
        except Exception:
            pass
        return None

    @staticmethod
    def _palm_up(hand: HandObservation) -> bool:
        """BareHands' wrist-to-middle-MCP vertical prayer test."""
        try:
            lm = getattr(hand, "landmarks", ()) or ()
            wrist, mcp = lm[0], lm[9]
            span = ((float(wrist[0]) - float(mcp[0])) ** 2
                    + (float(wrist[1]) - float(mcp[1])) ** 2) ** 0.5
            return bool(span > 0 and
                        (float(wrist[1]) - float(mcp[1])) / span > 0.85)
        except Exception:
            return False

    def update(self, hand: HandObservation, gesture: Gesture,
               timestamp: float) -> None:
        point = self.point_for(hand)
        if point is not None:
            self.history.append((float(timestamp), point[0], point[1]))
            self.last_point = point
        self.hand_up = self._palm_up(hand)
        if gesture == Gesture.POINTING:
            if self.pointing_started_at is None:
                self.pointing_started_at = float(timestamp)
                try:
                    self.pointing_start_point = HandInteractionMapper._point(
                        hand, gesture)
                except Exception:
                    self.pointing_start_point = point
                self.pointing_emitted = False
        else:
            if (self.pointing_started_at is not None
                    and not self.pointing_emitted
                    and gesture == Gesture.OPEN):
                self.last_point_release = {
                    "started": self.pointing_started_at,
                    "point": self.pointing_start_point,
                    "released_at": float(timestamp),
                }
            self.pointing_started_at = None
            self.pointing_start_point = None
            self.pointing_emitted = False
        if gesture == Gesture.OPEN:
            self.last_open_at = float(timestamp)
            self.last_soft_open_at = float(timestamp)
            self.claw_armed = False
            self.claw_emitted = False
            self.claw_streak = 0
        elif gesture == Gesture.CLAW:
            self.claw_streak += 1
            if (self.claw_streak >= 5 and not self.claw_armed
                    and self.last_open_at is not None
                    and timestamp - self.last_open_at <= 2.5):
                self.claw_armed = True
        else:
            self.claw_streak = max(0, self.claw_streak - 5)
            if self.claw_armed and self.claw_lost_at is None:
                self.claw_lost_at = float(timestamp)

        if gesture == Gesture.PINCH:
            if not self.pinched:
                self.pinch_started_at = float(timestamp)
                self.pinch_start_point = point
                try:
                    self.pinch_pointer_start_point = HandInteractionMapper._point(
                        hand, Gesture.PINCH)
                except Exception:
                    self.pinch_pointer_start_point = point
                self.press_emitted = False
                self.hold_emitted = False
                self.pinch_clicked = False
                self.last_release = None
            self.pinched = True
            self.last_pinch_at = float(timestamp)
        elif gesture != Gesture.UNKNOWN:
            # A single ambiguous classification frame does not release a
            # BareHands pinch; a real open/closed transition does.
            if self.pinched:
                self.last_release = {
                    "started": self.pinch_started_at,
                    "point": self.pinch_start_point,
                    "pointer_point": self.pinch_pointer_start_point,
                    "released_at": float(timestamp),
                }
            self.pinched = False
        self.last_seen_at = float(timestamp)


class HandStateBus:
    """Single-slot canonical hand state (replaced, never accumulated)."""

    def __init__(self, ttl_seconds: float = HAND_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._current: Optional[HandState] = None
        self._subscribers: list = []
        self.publish_count = 0

    def publish(self, state: HandState, source: str = "") -> HandState:
        try:
            if state is None:
                return state
            try:
                state.source = (source or "")[:80]
            except Exception:
                pass
            with self._lock:
                self._current = state
                self.publish_count += 1
                subs = list(self._subscribers)
            for fn in subs:
                try:
                    fn(state)
                except Exception as e:
                    log.warning(f"hand subscriber failed: {e}")
            return state
        except Exception as e:
            log.warning(f"hand publish failed: {e}")
            return state

    def get(self) -> Optional[HandState]:
        with self._lock:
            return self._current

    def get_fresh(self) -> Optional[HandState]:
        cur = self.get()
        if cur is None or cur.is_stale(self.ttl_seconds):
            return None
        return cur

    def subscribe(self, fn) -> Any:
        with self._lock:
            self._subscribers.append(fn)

        def _unsub():
            try:
                with self._lock:
                    if fn in self._subscribers:
                        self._subscribers.remove(fn)
            except Exception:
                pass

        return _unsub


@dataclass(frozen=True)
class HandInteraction:
    """One bounded semantic interpretation of one or two hand observations."""

    name: SemanticInteraction
    hand_ids: tuple
    gestures: tuple
    point: Optional[tuple] = None
    screen_point: Optional[tuple] = None
    start_point: Optional[tuple] = None
    end_point: Optional[tuple] = None
    action_type: str = ""
    action_parameters: dict = field(default_factory=dict)
    target_description: str = ""
    target: dict = field(default_factory=dict)
    stable: bool = False
    votes: int = 0
    frame_id: int = 0
    provenance: str = (
        "BareHands stage.html getCursor/beginGrab/endGrab, pinch "
        "hysteresis, two-hand stretch, deliberate clap, and open-to-claw "
        "transition; DUDE semantic proposal only")

    def to_dict(self) -> dict:
        return {
            "name": self.name.value,
            "hand_ids": list(self.hand_ids),
            "gestures": list(self.gestures),
            "point": self.point,
            "screen_point": self.screen_point,
            "start_point": self.start_point,
            "end_point": self.end_point,
            "action_type": self.action_type,
            "action_parameters": dict(self.action_parameters),
            "target_description": self.target_description,
            "target": dict(self.target),
            "stable": bool(self.stable),
            "votes": int(self.votes),
            "frame_id": int(self.frame_id),
            "provenance": self.provenance,
        }

    def with_target(self, target: Optional[dict],
                    screen_point: Optional[tuple] = None
                    ) -> "HandInteraction":
        if not target:
            return self
        return HandInteraction(
            name=self.name,
            hand_ids=self.hand_ids,
            gestures=self.gestures,
            point=self.point,
            screen_point=screen_point,
            start_point=self.start_point,
            end_point=self.end_point,
            action_type=self.action_type,
            action_parameters=dict(self.action_parameters),
            target_description=str(
                target.get("element") or self.target_description)[:240],
            target=dict(target),
            stable=self.stable,
            votes=self.votes,
            frame_id=self.frame_id,
            provenance=self.provenance,
        )


class HandInteractionMapper:
    """Adapt BareHands' proven temporal rules without executing anything.

    The mapper is bounded to two hands and a short history. It emits semantic
    proposals only; TaskEngine/ActionExecutor remain the sole execution path.
    Pinch release, drag, hold, flick, two-hand scale, and deliberate clap are
    based on the actual BareHands stage.html state machine (per-hand cursors,
    pinch hysteresis, tap/hold discrimination, two-hand distance scaling, and
    mutual-motion clap).
    """

    _HISTORY = 8
    # BareHands stage.html uses a ~300 ms near-still release for TAP and
    # promotes a carried pinch to HOLD after roughly one second.  DUDE's
    # normalized camera coordinates use a conservative 0.035 travel floor
    # (about the same as BareHands' 26 px target gate at its display scale).
    _PINCH_TAP_SECONDS = 0.30
    _PINCH_PRESS_SECONDS = 0.30
    _PINCH_STILL_WINDOW = 0.30
    _PINCH_STILL_TRAVEL = 0.08
    _PINCH_CONFIRM_DISTANCE = 0.035
    _PINCH_HOLD_SECONDS = 1.00
    # RGB hand tracking cannot see table contact.  A stable one-finger point
    # therefore uses a bounded dwell as its explicit click gesture.
    _POINT_DWELL_SECONDS = 0.40
    _POINT_STILL_WINDOW = 0.40
    _POINT_STILL_TRAVEL = 0.08
    _POINT_RELEASE_SECONDS = 0.40
    _POINT_RELEASE_RECOGNITION_GRACE = 0.60
    _DRAG_DISTANCE = 0.035
    _FLICK_SPEED = 2.0
    _SCALE_MIN_DELTA = 0.015
    _CLAP_COOLDOWN = 1.5
    _THREE_FINGER_MIN_TRAVEL = 0.18
    _THREE_FINGER_MAX_HORIZONTAL = 0.12
    _THREE_FINGER_COOLDOWN = 1.5

    def __init__(self):
        self._lock = threading.RLock()
        self._stabilizers: dict = {}
        self._history: dict = {}
        self._pinches: dict = {}
        self._last_flick: dict = {}
        self._last_three_minimize: dict = {}
        self._active_fingers: dict = {}
        self._smoothed_points: dict = {}
        self._claw_active: dict = {}
        self._clap_history = collections.deque(maxlen=self._HISTORY)
        self._last_clap = 0.0
        self._dual_distances: dict = {}
        self._dual_baselines: dict = {}
        self._dual_emitted: set = set()
        self._cursor_states: dict = {}
        self._last_cursor_snapshot: tuple = ()
        self._clap_pair = None
        self.last_interaction: Optional[HandInteraction] = None

    @staticmethod
    def _point(hand: HandObservation,
               gesture: Optional[Gesture] = None) -> Optional[tuple]:
        # Static helper retained for BareHands' temporal cursor state. The
        # stateful mapper below adds per-hand hysteresis for live pointers.
        finger = (pointing_finger(hand)
                  if gesture == Gesture.POINTING else "index")
        point = finger_point(hand, finger)
        if point is not None:
            return point
        try:
            point = getattr(hand, "palm", ()) or getattr(hand, "wrist", ())
            if point and len(point) >= 2:
                return (float(point[0]), float(point[1]))
        except Exception:
            pass
        return None

    def _point_for(self, hand: HandObservation, gesture: Gesture,
                   identity: str) -> Optional[tuple]:
        if gesture == Gesture.POINTING:
            finger = pointing_finger(
                hand, self._active_fingers.get(identity))
            self._active_fingers[identity] = finger
        else:
            finger = "index"
        if gesture == Gesture.THREE_FINGER:
            tips = [finger_point(hand, name) for name in
                    ("index", "middle", "ring")]
            tips = [tip for tip in tips if tip is not None]
            if tips:
                raw = (sum(float(tip[0]) for tip in tips) / len(tips),
                       sum(float(tip[1]) for tip in tips) / len(tips))
            else:
                raw = finger_point(hand, "index")
        else:
            raw = finger_point(hand, finger)
        if gesture in (Gesture.POINTING, Gesture.PINCH, Gesture.THREE_FINGER):
            smoothed = smooth_point(self._smoothed_points.get(identity), raw)
            if smoothed is not None:
                self._smoothed_points[identity] = smoothed
            return smoothed or self._point(hand)
        self._smoothed_points[identity] = raw
        return raw or self._point(hand)

    @staticmethod
    def _distance(a: Optional[tuple], b: Optional[tuple]) -> float:
        if not a or not b:
            return 0.0
        return _d(a, b)

    def _recently_still(self, identity: str, point: Optional[tuple],
                        timestamp: float, window: float,
                        tolerance: float) -> bool:
        if point is None:
            return False
        history = [item for item in self._history.get(identity, ())
                   if timestamp - float(item[0]) <= window]
        if len(history) < 2:
            return False
        return self._distance(
            (history[0][1], history[0][2]), point) <= tolerance

    @staticmethod
    def _gesture_value(value) -> str:
        return str(getattr(value, "value", value) or "unknown").lower()

    @classmethod
    def _identity(cls, hand: HandObservation, index: int) -> str:
        identity = str(getattr(hand, "identity", "") or "").strip().lower()
        if identity:
            return identity
        label = str(getattr(hand, "label", "") or "").strip().lower()
        return label if label in {"left", "right"} else f"hand_{index}"

    def _none(self, state: Optional[HandState] = None) -> HandInteraction:
        return HandInteraction(
            name=SemanticInteraction.NONE,
            hand_ids=tuple(getattr(state, "identities", lambda: ())()),
            gestures=(),
            frame_id=int(getattr(state, "frame_id", 0) or 0),
        )

    def observe(
        self,
        state: Optional[HandState],
        stable_by_hand: Optional[dict] = None,
        now: Optional[float] = None,
    ) -> HandInteraction:
        """Interpret one fresh state, returning a semantic proposal only."""
        try:
            with self._lock:
                timestamp = float(time.time() if now is None else now)
                if state is None:
                    result = self._none()
                    self.last_interaction = result
                    return result
                hands = list(getattr(state, "hands", ()) or ())[:MAX_HANDS]
                previous_cursors = tuple(self._cursor_states.items())
                if not hands:
                    result = self._maybe_clap_disappearance(
                        previous_cursors, timestamp)
                    self._history.clear()
                    self._pinches.clear()
                    self._active_fingers.clear()
                    self._last_three_minimize.clear()
                    self._smoothed_points.clear()
                    self._dual_distances.clear()
                    self._dual_baselines.clear()
                    self._dual_emitted.clear()
                    self._cursor_states.clear()
                    self._claw_active.clear()
                    self._clap_history.clear()
                    self._clap_pair = None
                    self._last_cursor_snapshot = ()
                    if result is None:
                        result = self._none(state)
                    self.last_interaction = result
                    return result

                current_ids = set()
                stable: dict = {}
                points: dict = {}
                for index, hand in enumerate(hands):
                    key = self._identity(hand, index)
                    current_ids.add(key)
                    if stable_by_hand is not None and key in stable_by_hand:
                        stable[key] = tuple(stable_by_hand[key])
                    else:
                        stabilizer = self._stabilizers.setdefault(
                            key, HandStabilizer())
                        raw = getattr(hand, "gesture", Gesture.UNKNOWN)
                        stable[key] = stabilizer.update(raw)
                    cursor = self._cursor_states.setdefault(
                        key, BareHandsCursorState(key))
                    cursor.update(hand, stable[key][0], timestamp)
                    point = (self._point_for(hand, stable[key][0], key)
                             or cursor.last_point)
                    points[key] = point
                    history = self._history.setdefault(
                        key, collections.deque(maxlen=self._HISTORY))
                    if point is not None:
                        history.append((timestamp, point[0], point[1]))

                # Missing hands are not allowed to inherit a prior gesture;
                # a surviving hand keeps its own independent state.
                if len(hands) < 2 and len(previous_cursors) >= 2:
                    vanished = self._maybe_clap_disappearance(
                        previous_cursors, timestamp)
                    if vanished is not None:
                        self._dual_distances.clear()
                        self._dual_baselines.clear()
                        self._dual_emitted.clear()
                        for key in list(self._cursor_states):
                            if key not in current_ids:
                                self._cursor_states.pop(key, None)
                        self._last_cursor_snapshot = tuple(
                            self._cursor_states.items())
                        self._last_interaction = vanished
                        return vanished
                for key in list(self._history):
                    if key not in current_ids:
                        self._history.pop(key, None)
                        self._pinches.pop(key, None)
                        self._active_fingers.pop(key, None)
                        self._last_three_minimize.pop(key, None)
                        self._smoothed_points.pop(key, None)
                        self._claw_active.pop(key, None)
                for key in list(self._cursor_states):
                    if key not in current_ids:
                        self._cursor_states.pop(key, None)
                if len(hands) < 2:
                    self._dual_distances.clear()
                    self._dual_baselines.clear()
                    self._dual_emitted.clear()
                self._last_cursor_snapshot = tuple(
                    self._cursor_states.items())

                if len(hands) >= 2:
                    dual = self._observe_two_hands(
                        state, hands, stable, points, timestamp)
                    if dual is not None:
                        self.last_interaction = dual
                        return dual
                    single_actions = []
                    for hand in hands:
                        single_state = HandState(
                            hands=(hand,), frame_id=int(getattr(
                                state, "frame_id", 0) or 0))
                        candidate = self._observe_one_hand(
                            single_state, (hand,), stable, points, timestamp)
                        if candidate.action_type:
                            single_actions.append(candidate)
                    if len(single_actions) == 1:
                        result = single_actions[0]
                        self.last_interaction = result
                        return result
                    # Mixed two-hand poses remain two-hand state. Do not
                    # collapse them into a primary-only interaction while
                    # one hand is pinch/open/pointing independently.
                    ids = tuple(
                        self._identity(hand, index)
                        for index, hand in enumerate(hands))
                    names = tuple(
                        self._gesture_value(stable.get(identity,
                                                         (Gesture.UNKNOWN, 0))[0])
                        for identity in ids)
                    result = HandInteraction(
                        name=SemanticInteraction.NONE,
                        hand_ids=ids, gestures=names,
                        point=points.get(ids[0]),
                        stable=True,
                        votes=min((int(stable.get(identity,
                                                   (Gesture.UNKNOWN, 0))[1] or 0)
                                   for identity in ids), default=0),
                        frame_id=int(getattr(state, "frame_id", 0) or 0),
                    )
                    self.last_interaction = result
                    return result

                result = self._observe_one_hand(
                    state, hands, stable, points, timestamp)
                self.last_interaction = result
                return result
        except Exception as exc:
            log.warning("hand interaction mapping failed: %s", exc)
            result = self._none(state)
            self.last_interaction = result
            return result

    def _observe_one_hand(self, state, hands, stable, points, timestamp
                          ) -> HandInteraction:
        # Pick a stable gesture without erasing the other hand's observation.
        candidates = []
        for index, hand in enumerate(hands):
            key = self._identity(hand, index)
            gesture, votes = stable.get(key, (Gesture.UNKNOWN, 0))
            candidates.append((index, hand, key, gesture, votes))
        index, hand, key, gesture, votes = max(
            candidates,
            key=lambda item: (int(item[4] or 0),
                              float(getattr(item[1], "handedness_score", 0.0))))
        point = points.get(key)
        gesture_name = self._gesture_value(gesture)
        frame_id = int(getattr(state, "frame_id", 0) or 0)
        base = (key, gesture_name, point, int(votes or 0), frame_id)

        cursor = self._cursor_states.get(key)
        if gesture == Gesture.PINCH:
            pinch_distance = getattr(hand, "pinch_distance", None)
            if (pinch_distance is not None
                    and float(pinch_distance) > self._PINCH_CONFIRM_DISTANCE):
                # A stale stabilizer vote must not turn a loose near-pinch
                # into a click. Re-arm once the tips are actually closed.
                if cursor is not None:
                    cursor.pinched = False
                    cursor.pinch_started_at = None
                    cursor.press_emitted = False
                return HandInteraction(
                    name=SemanticInteraction.POINTER,
                    hand_ids=(key,), gestures=("pointing",), point=point,
                    target_description="hand pointer target",
                    stable=True, votes=votes, frame_id=frame_id)
            # BareHands keeps a pinch latched until release.  A hold is a
            # one-shot semantic event at the actual hold threshold, not a
            # pose repeated on every stabilized frame.
            if cursor is not None and cursor.pinch_started_at is not None:
                held_for = max(0.0, timestamp - cursor.pinch_started_at)
                if (held_for >= self._PINCH_PRESS_SECONDS
                        and not cursor.press_emitted):
                    cursor.press_emitted = True
                    # This press already performs the click, so the later
                    # release must not click a second time. Without this the
                    # same pinch produced two clicks.
                    cursor.pinch_clicked = True
                    return HandInteraction(
                        name=SemanticInteraction.TAP,
                        hand_ids=(key,), gestures=(gesture_name,),
                        point=point,
                        start_point=cursor.pinch_pointer_start_point,
                        end_point=point, action_type="click_at",
                        target_description="hand pinch press target",
                        stable=True, votes=votes, frame_id=frame_id)
                if (held_for >= self._PINCH_HOLD_SECONDS
                        and not cursor.hold_emitted):
                    cursor.hold_emitted = True
                    return HandInteraction(
                        name=SemanticInteraction.HOLD,
                        hand_ids=(key,), gestures=(gesture_name,),
                        point=point,
                        start_point=cursor.pinch_pointer_start_point,
                        end_point=point,
                        target_description="hand pinch hold target",
                        stable=True, votes=votes, frame_id=frame_id)
            return HandInteraction(
                name=SemanticInteraction.NONE,
                hand_ids=(key,), gestures=(gesture_name,),
                point=point, stable=True, votes=votes, frame_id=frame_id)

        if gesture == Gesture.THREE_FINGER:
            history = list(self._history.get(key, ()) or ())
            if len(history) >= 2 and point is not None:
                start = history[max(0, len(history) - 4)]
                dx = point[0] - start[1]
                dy = point[1] - start[2]
                last = self._last_three_minimize.get(key, 0.0)
                steps = [
                    history[i + 1][2] - history[i][2]
                    for i in range(max(0, len(history) - 4), len(history) - 1)
                ]
                downward = (len(steps) >= 2
                            and sum(step > 0.01 for step in steps) >= 2)
                if (votes >= 4
                        and downward
                        and dy >= self._THREE_FINGER_MIN_TRAVEL
                        and abs(dx) <= self._THREE_FINGER_MAX_HORIZONTAL
                        and timestamp - last >= self._THREE_FINGER_COOLDOWN):
                    self._last_three_minimize[key] = timestamp
                    return HandInteraction(
                        name=SemanticInteraction.WINDOW_MINIMIZE,
                        hand_ids=(key,), gestures=("three_finger",),
                        point=point, start_point=(start[1], start[2]),
                        end_point=point, action_type="window_operation",
                        action_parameters={"action": "minimize"},
                        target_description="three finger downward minimize target",
                        stable=True, votes=votes, frame_id=frame_id)
            return HandInteraction(
                name=SemanticInteraction.POINTER,
                hand_ids=(key,), gestures=("three_finger",), point=point,
                target_description="three finger minimize pointer",
                stable=True, votes=votes, frame_id=frame_id)

        if gesture == Gesture.CLAW:
            if (cursor is not None and cursor.claw_armed
                    and not cursor.claw_emitted):
                cursor.claw_emitted = True
                self._claw_active[key] = True
                return HandInteraction(
                    name=SemanticInteraction.CLAW,
                    hand_ids=(key,), gestures=(gesture_name,), point=point,
                    action_type="",
                    target_description="3D claw hold target",
                    stable=True, votes=votes, frame_id=frame_id)
            # A claw that has travelled is a drag, so an application can be
            # moved with the claw exactly as it can with a pinch-and-move.
            origins = getattr(self, "_claw_origin", None)
            if origins is None:
                origins = {}
                self._claw_origin = origins
            if self._claw_active.get(key) and point is not None:
                origin = origins.get(key)
                if origin is None and cursor is not None:
                    # Claw just armed: remember where the drag will begin.
                    origin = self._point_for(hand, gesture, key)
                    if origin is not None:
                        origins[key] = tuple(origin)
                if origin is not None:
                    ox, oy = float(origin[0]), float(origin[1])
                    if self._distance((ox, oy), point) >= self._DRAG_DISTANCE:
                        origins.pop(key, None)
                        return HandInteraction(
                            name=SemanticInteraction.DRAG,
                            hand_ids=(key,), gestures=(gesture_name,),
                            point=point, start_point=(ox, oy), end_point=point,
                            action_type="drag",
                            action_parameters={
                                "x0": ox, "y0": oy,
                                "x1": float(point[0]), "y1": float(point[1]),
                            },
                            target_description="hand claw drag target",
                            stable=True, votes=votes, frame_id=frame_id)
            return HandInteraction(
                name=SemanticInteraction.NONE,
                hand_ids=(key,), gestures=(gesture_name,), point=point,
                stable=True, votes=votes, frame_id=frame_id)
        self._claw_active.pop(key, None)

        pinch = None
        if cursor is not None:
            pinch = cursor.last_release
            cursor.last_release = None
        self._pinches.pop(key, None)
        if pinch is not None and point is not None and pinch.get("started"):
            duration = max(0.0, timestamp - float(pinch["started"]))
            start = (pinch.get("pointer_point")
                     or pinch.get("point") or point)
            travel = self._distance(start, point)
            # NOTE: there is deliberately no "press already emitted" veto here.
            # That guard returned NONE for every completed pinch once a press
            # had been seen, so a recognised pinch (score 0.99, stable at 5
            # votes) was discarded and never became a click or a drag. The
            # dedupe that matters lives in the cursor's one-shot flags
            # (hold_emitted / press_emitted) which are reset per gesture, and
            # the sink already suppresses a repeated identical action.
            # NOTE: the old "press already emitted -> NONE" veto is gone. It
            # discarded EVERY completed pinch, so a gesture recognised at 0.99
            # confidence never became a click. Duplicate suppression now uses
            # the per-gesture pinch_clicked flag, which resets when a new pinch
            # starts, so one pinch still yields exactly one action.
            if cursor is not None and cursor.pinch_clicked:
                return HandInteraction(
                    name=SemanticInteraction.NONE,
                    hand_ids=base[0:1], gestures=base[1:2],
                    point=point, start_point=start, end_point=point,
                    stable=True, votes=votes, frame_id=frame_id)
            if travel >= self._DRAG_DISTANCE:
                # Pinch-and-move is a DRAG, not a click. This branch used to
                # return action_type="click_at", so dragging a window just
                # clicked wherever the finger happened to finish instead of
                # moving the application.
                if cursor is not None:
                    cursor.pinch_clicked = True
                return HandInteraction(
                    name=SemanticInteraction.DRAG,
                    hand_ids=base[0:1], gestures=base[1:2],
                    point=point, start_point=start, end_point=point,
                    action_type="drag",
                    action_parameters={
                        "x0": float(start[0]), "y0": float(start[1]),
                        "x1": float(point[0]), "y1": float(point[1]),
                    },
                    target_description="hand pinch drag target",
                    stable=True, votes=votes, frame_id=frame_id)
            if duration <= self._PINCH_TAP_SECONDS:
                if cursor is not None:
                    cursor.pinch_clicked = True
                return HandInteraction(
                    name=SemanticInteraction.TAP,
                    hand_ids=base[0:1], gestures=base[1:2],
                    point=point, start_point=start, end_point=point,
                    action_type="click_at",
                    target_description="hand pinch target",
                    stable=True, votes=votes, frame_id=frame_id)
            # A completed pinch is a click. Previously only a SHORT tap
            # (<= _PINCH_TAP_SECONDS) or a long hold produced an action, so an
            # ordinary medium-length pinch-and-release fell out of every branch
            # and the gesture was discarded - the dot moved but nothing was
            # clicked. Any release without meaningful travel now clicks, which
            # is what a hand expects from a cursor.
            if duration < self._PINCH_HOLD_SECONDS:
                if cursor is not None:
                    cursor.pinch_clicked = True
                return HandInteraction(
                    name=SemanticInteraction.TAP,
                    hand_ids=base[0:1], gestures=base[1:2],
                    point=point, start_point=start, end_point=point,
                    action_type="click_at",
                    target_description="hand pinch target",
                    stable=True, votes=votes, frame_id=frame_id)
            if duration >= self._PINCH_HOLD_SECONDS:
                if cursor is not None and cursor.hold_emitted:
                    if cursor is not None:
                        cursor.pinch_clicked = True
                    return HandInteraction(
                        name=SemanticInteraction.TAP,
                        hand_ids=base[0:1], gestures=base[1:2],
                        point=point, start_point=start, end_point=point,
                        action_type="click_at",
                        target_description="hand pinch hold-release target",
                        stable=True, votes=votes, frame_id=frame_id)
                if cursor is not None:
                    cursor.pinch_clicked = True
                return HandInteraction(
                    name=SemanticInteraction.HOLD,
                    hand_ids=base[0:1], gestures=base[1:2],
                    point=point, start_point=start, end_point=point,
                    target_description="hand pinch hold target",
                    stable=True, votes=votes, frame_id=frame_id)

        point_release = None
        if cursor is not None:
            point_release = cursor.last_point_release
            cursor.last_point_release = None
        if (point_release is not None and point is not None
                and point_release.get("started")):
            point_duration = max(
                0.0, timestamp - float(point_release["started"]))
            point_start = point_release.get("point") or point
            point_travel = self._distance(point_start, point)
            if (point_duration <= (
                    self._POINT_RELEASE_SECONDS
                    + self._POINT_RELEASE_RECOGNITION_GRACE) + 1e-6
                    and point_travel <= self._POINT_STILL_TRAVEL):
                return HandInteraction(
                    name=SemanticInteraction.TAP,
                    hand_ids=base[0:1], gestures=base[1:2],
                    point=point, start_point=point_start, end_point=point,
                    action_type="click_at",
                    target_description="hand point release target",
                    stable=True, votes=votes, frame_id=frame_id)

        if gesture == Gesture.POINTING and point is not None:
            # A single-finger point is the RGB-safe click path.  Holding the
            # same fingertip still for the dwell interval emits one semantic
            # tap; moving the finger cancels the pending dwell by changing the
            # start point on the next stable gesture.
            if cursor is not None and cursor.pointing_started_at is not None:
                held_for = max(0.0, timestamp - cursor.pointing_started_at)
                recently_still = self._recently_still(
                    key, point, timestamp, self._POINT_STILL_WINDOW,
                    self._POINT_STILL_TRAVEL)
                if (held_for + 1e-6 >= self._POINT_DWELL_SECONDS
                        and recently_still
                        and not cursor.pointing_emitted):
                    cursor.pointing_emitted = True
                    return HandInteraction(
                        name=SemanticInteraction.TAP,
                        hand_ids=base[0:1], gestures=base[1:2],
                        point=point, start_point=cursor.pointing_start_point,
                        end_point=point, action_type="click_at",
                        target_description="hand point dwell target",
                        stable=True, votes=votes, frame_id=frame_id)
            return HandInteraction(
                name=SemanticInteraction.POINTER,
                hand_ids=(key,), gestures=(gesture_name,), point=point,
                target_description="hand pointer target",
                stable=True, votes=votes, frame_id=frame_id)

        # BareHands' deliberate flick is a bounded open-hand velocity event;
        # it maps only to the existing scroll action, never to pyautogui here.
        if gesture == Gesture.OPEN and point is not None:
            history = list(self._history.get(key, ()) or ())
            if len(history) >= 2 and timestamp - history[-2][0] > 0:
                dt = timestamp - history[-2][0]
                dx = point[0] - history[-2][1]
                dy = point[1] - history[-2][2]
                speed = ((dx * dx + dy * dy) ** 0.5) / max(dt, 0.001)
                last = self._last_flick.get(key, 0.0)
                if speed >= self._FLICK_SPEED and timestamp - last >= 0.5:
                    self._last_flick[key] = timestamp
                    return HandInteraction(
                        name=SemanticInteraction.FLICK,
                        hand_ids=(key,), gestures=(gesture_name,), point=point,
                        start_point=(history[-2][1], history[-2][2]),
                        end_point=point, action_type="",
                        action_parameters={},
                        target_description="hand flick observed (non-executing)",
                        stable=True, votes=votes, frame_id=frame_id)

        return HandInteraction(
            name=SemanticInteraction.NONE,
            hand_ids=(key,), gestures=(gesture_name,), point=point,
            stable=True, votes=votes, frame_id=frame_id)

    def retry_failed(self, interaction: HandInteraction) -> None:
        """Re-arm a one-shot proposal when UIA grounding rejected it.

        Gesture recognition is not action success.  If the semantic event was
        recognized but the existing visual/TaskEngine boundary refused it,
        the next fresh frame may retry the same episode; accepted proposals
        remain one-shot and are still deduplicated by HandTracker.
        """
        try:
            if interaction.name == SemanticInteraction.TWO_HAND_SCALE:
                self._dual_emitted.discard(tuple(sorted(
                    interaction.hand_ids)))
            elif interaction.name == SemanticInteraction.CLAW:
                for identity in interaction.hand_ids:
                    cursor = self._cursor_states.get(identity)
                    if cursor is not None:
                        cursor.claw_emitted = False
            elif interaction.name == SemanticInteraction.TAP:
                for identity in interaction.hand_ids:
                    cursor = self._cursor_states.get(identity)
                    if cursor is None:
                        continue
                    target_description = str(getattr(
                        interaction, "target_description", ""))
                    if ("point dwell" in target_description
                            or "point release" in target_description):
                        # A rejected target must be allowed to try again when
                        # the same finger remains over the next valid target.
                        cursor.pointing_emitted = False
                        cursor.pointing_started_at = None
                    elif ("pinch press" in target_description
                          or "pinch hold-release" in target_description):
                        cursor.press_emitted = False
        except Exception:
            pass

    def _maybe_clap_disappearance(self, previous_cursors, timestamp
                                  ) -> Optional[HandInteraction]:
        """Honor BareHands' qualified vanish-at-contact clap read."""
        try:
            if len(previous_cursors) < 2:
                return None
            cursors = [item[1] for item in previous_cursors[:2]]
            if any(cursor.last_seen_at is None
                   or timestamp - cursor.last_seen_at > 0.2
                   for cursor in cursors):
                return None
            if not all(
                    cursor.last_soft_open_at is not None
                    and timestamp - cursor.last_soft_open_at <= 0.25
                    and cursor.hand_up
                    and not cursor.pinched
                    and (cursor.last_pinch_at is None
                         or timestamp - cursor.last_pinch_at >= 0.8)
                    for cursor in cursors):
                return None
            points = [cursor.last_point for cursor in cursors]
            if any(point is None for point in points):
                return None
            distance = self._distance(points[0], points[1])
            was_apart = any(
                self._distance((a[1], a[2]), (b[1], b[2])) > 0.18
                for a in cursors[0].history
                for b in cursors[1].history
                if a[0] >= timestamp - 0.8 and b[0] >= timestamp - 0.8)
            if (distance < 0.16 and was_apart
                    and timestamp - self._last_clap >= self._CLAP_COOLDOWN):
                self._last_clap = timestamp
                ids = tuple(item[0] for item in previous_cursors[:2])
                return HandInteraction(
                    name=SemanticInteraction.CLAP,
                    hand_ids=ids,
                    gestures=("open", "open"),
                    point=((points[0][0] + points[1][0]) / 2.0,
                           (points[0][1] + points[1][1]) / 2.0),
                    target_description="two-hand confirmation target",
                    action_type="click_at", stable=True, votes=3,
                    frame_id=0)
        except Exception:
            pass
        return None

    def _observe_two_hands(self, state, hands, stable, points, timestamp
                           ) -> Optional[HandInteraction]:
        first, second = hands[0], hands[1]
        first_key = self._identity(first, 0)
        second_key = self._identity(second, 1)
        first_gesture, first_votes = stable.get(
            first_key, (Gesture.UNKNOWN, 0))
        second_gesture, second_votes = stable.get(
            second_key, (Gesture.UNKNOWN, 0))
        first_point = points.get(first_key)
        second_point = points.get(second_key)
        frame_id = int(getattr(state, "frame_id", 0) or 0)
        ids = (first_key, second_key)
        gestures = (self._gesture_value(first_gesture),
                    self._gesture_value(second_gesture))

        first_cursor = self._cursor_states.get(first_key)
        second_cursor = self._cursor_states.get(second_key)
        if (first_gesture == Gesture.PINCH and
                second_gesture == Gesture.PINCH and
                first_cursor is not None and second_cursor is not None and
                first_cursor.pinched and second_cursor.pinched and
                first_point is not None and second_point is not None):
            distance = self._distance(first_point, second_point)
            pair_key = tuple(sorted(ids))
            previous = self._dual_distances.get(pair_key)
            baseline = self._dual_baselines.setdefault(pair_key, distance)
            self._dual_distances[pair_key] = distance
            if (previous is not None and
                    abs(distance - previous) >= self._SCALE_MIN_DELTA and
                    pair_key not in self._dual_emitted):
                scale_factor = max(0.25, min(4.0, distance / max(baseline, 0.001)))
                self._dual_emitted.add(pair_key)
                return HandInteraction(
                    name=SemanticInteraction.TWO_HAND_SCALE,
                    hand_ids=ids, gestures=gestures,
                    point=((first_point[0] + second_point[0]) / 2.0,
                           (first_point[1] + second_point[1]) / 2.0),
                    start_point=first_point, end_point=second_point,
                    action_type="resize_window",
                    action_parameters={
                        "scale_factor": scale_factor,
                        "distance_start": baseline,
                        "distance_end": distance,
                    },
                    target_description="two-hand scale target",
                    stable=True, votes=min(first_votes, second_votes),
                    frame_id=frame_id)
            # A stationary two-hand pinch is not a desktop action. Let the
            # independent one-hand observations below handle a deliberate
            # click; only an actual measured stretch becomes TWO_HAND_SCALE.
            return None

        # BareHands requires two open, vertical palms that were previously
        # apart and are now close. It is a confirmation semantic, so the
        # bounded bridge may turn it into one ordinary DUDE click proposal.
        if (first_gesture == Gesture.OPEN and
                second_gesture == Gesture.OPEN and
                first_cursor is not None and second_cursor is not None and
                first_point is not None and second_point is not None):
            pair_key = tuple(sorted(ids))
            if self._clap_pair != pair_key:
                self._clap_history.clear()
                self._clap_pair = pair_key
            recent_pinch = any(
                cursor.last_pinch_at is not None
                and timestamp - cursor.last_pinch_at < 0.8
                for cursor in (first_cursor, second_cursor))
            both_open = all(
                cursor.last_soft_open_at is not None
                and timestamp - cursor.last_soft_open_at <= 0.25
                for cursor in (first_cursor, second_cursor))
            both_up = first_cursor.hand_up and second_cursor.hand_up
            if not (both_open and both_up) or recent_pinch \
                    or first_cursor.pinched or second_cursor.pinched:
                self._clap_history.clear()
            else:
                distance = self._distance(first_point, second_point)
                self._clap_history.append((timestamp, distance))
                while (self._clap_history and
                       timestamp - self._clap_history[0][0] > 0.9):
                    self._clap_history.popleft()
                was_apart = any(
                    old_distance > 0.18 for _old_time, old_distance
                    in self._clap_history)
                approach = False
                if len(first_cursor.history) >= 2 \
                        and len(second_cursor.history) >= 2:
                    a0, a1 = first_cursor.history[-2], first_cursor.history[-1]
                    b0, b1 = second_cursor.history[-2], second_cursor.history[-1]
                    left_toward = a1[1] > a0[1]
                    right_toward = b1[1] < b0[1]
                    approach = left_toward and right_toward
                if (distance < 0.11 and was_apart and approach and
                        timestamp - self._last_clap >= self._CLAP_COOLDOWN):
                    self._last_clap = timestamp
                    self._clap_history.clear()
                    return HandInteraction(
                        name=SemanticInteraction.CLAP,
                        hand_ids=ids, gestures=gestures,
                        point=((first_point[0] + second_point[0]) / 2.0,
                               (first_point[1] + second_point[1]) / 2.0),
                        target_description="two-hand confirmation target",
                        action_type="click_at",
                        stable=True, votes=min(first_votes, second_votes),
                        frame_id=frame_id)
        else:
            self._clap_history.clear()
        pair_key = tuple(sorted(ids))
        self._dual_distances.pop(pair_key, None)
        self._dual_baselines.pop(pair_key, None)
        self._dual_emitted.discard(pair_key)
        return None


class HandActionBridge:
    """Convert a semantic proposal into the existing Action/TaskEngine model."""

    def __init__(self, task_engine: Any):
        self.task_engine = task_engine

    def __call__(self, interaction: HandInteraction) -> bool:
        action_type = str(getattr(interaction, "action_type", "") or "").lower()
        if not action_type:
            return False
        task = getattr(self.task_engine, "task_state", None)
        task_id = (str(getattr(task, "task_id", ""))
                   if task is not None else "")
        target_evidence = dict(getattr(interaction, "target", {}) or {})
        if not target_evidence:
            # Raw camera coordinates are never allowed to become an
            # executable click/drag. Require fresh existing visual truth.
            log.info(
                "HANDDROP act=%s point=%s reason=no_grounded_target",
                action_type,
                getattr(interaction, "point", None))
            return False
        from .state import (
            Action, ExpectedResult, GroundingMethod, RiskLevel, TargetSpec,
            VerificationMethod,
        )
        point = (getattr(interaction, "screen_point", None)
                 or getattr(interaction, "point", None))
        if not point or len(point) != 2:
            # No absolute screen position means there is nothing truthful to
            # click.  Never fall back to the raw camera-normalized point.
            return False
        parameters = dict(getattr(interaction, "action_parameters", {}) or {})
        parameters["hand_semantic"] = True
        target_name = str(target_evidence.get("element", "") or "")[:120]
        expected = ExpectedResult()
        verification_method = VerificationMethod.SCREEN_DELTA
        # The fingertip already resolved the exact screen point (and, via
        # WindowFromPoint, the exact window beneath it).  Grounding the click
        # by control NAME would re-resolve that name somewhere else on the
        # desktop and move the click off the dot, so the absolute coordinate
        # is authoritative for hand input.
        target = TargetSpec(coordinates=point)
        if action_type == "window_operation":
            window = target_evidence.get("window") or ()
            title = str(window[1] if len(window) > 1 else
                        target_evidence.get("element", "") or "")[:160]
            operation = str(parameters.get("action", "minimize")
                            or "minimize").lower()
            if (not title or operation not in {
                    "restore", "maximize", "minimize", "focus", "close"}):
                return False
            parameters.update({"action": operation, "title": title})
            target = TargetSpec(window_title_regex=title, coordinates=point)
            expected = ExpectedResult(window_title=title)
            verification_method = VerificationMethod.SCREEN_DELTA
        elif action_type == "resize_window":
            window = target_evidence.get("window") or ()
            title = str(window[1] if len(window) > 1 else
                        target_evidence.get("element", "") or "")[:160]
            window_rect = tuple(target_evidence.get("window_rect") or ())
            scale = float(parameters.get("scale_factor", 1.0) or 1.0)
            if (not title or len(window_rect) != 4 or
                    not (0.25 <= scale <= 4.0)):
                return False
            x, y, width, height = [int(round(float(v))) for v in window_rect]
            new_w = max(120, min(10000, int(round(width * scale))))
            new_h = max(80, min(10000, int(round(height * scale))))
            parameters.update({
                "title": title,
                "x": x,
                "y": y,
                "w": new_w,
                "h": new_h,
                "expected_w": new_w,
                "expected_h": new_h,
            })
            target = TargetSpec(window_title_regex=title, coordinates=point)
            expected = ExpectedResult(window_title=title)
            verification_method = VerificationMethod.WINDOW_GEOMETRY_CHANGED
        action = Action(
            task_id=task_id,
            step_id=int(getattr(task, "current_step", 0) or 0),
            intent=str(getattr(interaction, "name", "hand interaction")),
            action_type=action_type,
            target=target,
            target_description=str(getattr(
                interaction, "target_description", "hand interaction"))[:240],
            grounding_method=GroundingMethod.ABSOLUTE_COORDS,
            expected_result=expected,
            verification_method=verification_method,
            confidence=max(0.0, min(1.0, float(getattr(
                interaction, "votes", 0) or 0) / 5.0)),
            risk_level=RiskLevel.LOW,
            side_effect_class="NON_IDEMPOTENT",
            replay_policy="DO_NOT_RETRY",
            parameters=parameters,
        )
        dispatch = getattr(self.task_engine, "dispatch_hand_action", None)
        if callable(dispatch):
            ok = bool(dispatch(action))
            log.info("HANDDISPATCH act=%s xy=%s target=%r accepted=%s",
                     action_type, point, target_name or None, ok)
            return ok
        propose = getattr(self.task_engine, "propose_hand_action", None)
        if not callable(propose):
            log.info("HANDDROP act=%s reason=no_task_engine_sink",
                     action_type)
            return False
        ok = bool(propose(action))
        log.info("HANDDISPATCH act=%s xy=%s target=%r proposed=%s",
                 action_type, point, target_name or None, ok)
        return ok


class LensCoverDetector:
    """Dark + flat sustained frames mean a covered lens, not an error.

    A covered camera (slider, cap, finger pressed on it) yields near-
    black, nearly uniform frames. That is sensor information, not a
    failure: report it, keep streaming (it may uncover), never crash.
    Bright-flat scenes (walls) are NOT covers — only dark-flat counts,
    and the verdict is always labeled suspected, never certain.
    """

    def __init__(self, need: int = 10, max_mean: float = 20.0,
                 max_std: float = 10.0):
        self._need = need
        self._max_mean = max_mean
        self._max_std = max_std
        self._run = 0
        self.covered = False
        self._lock = threading.Lock()

    def update(self, mean: float, std: float) -> bool:
        """Feed one frame's stats; returns covered-suspected or not."""
        try:
            with self._lock:
                if mean <= self._max_mean and std <= self._max_std:
                    self._run += 1
                else:
                    self._run = 0
                self.covered = self._run >= self._need
                return self.covered
        except Exception:
            return False

    def reset(self) -> None:
        try:
            with self._lock:
                self._run = 0
                self.covered = False
        except Exception:
            pass


class HandCamera:
    """Explicit, stoppable, non-blocking shared-capability camera."""

    # Consecutive failed frame reads before the capture handle is re-opened.
    READ_FAILURE_LIMIT = 10
    # Bounded re-open attempts, with a growing backoff so a genuinely absent
    # device stops retrying instead of hammering the capture driver.
    RECOVER_ATTEMPTS = 5
    RECOVER_BACKOFF_SECONDS = 1.5

    def __init__(self, index: int = 0, fps: float = 10.0,
                 backend_preference: str = "auto"):
        self.index = index
        self.interval = 1.0 / max(1.0, fps)
        self.backend_preference = str(backend_preference or "auto").lower()
        self._lock = threading.Lock()
        self._frame = None  # single working slot (numpy, replaced)
        self._frame_id = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.last_error = ""
        self.frames_seen = 0
        self._read_failures = 0
        self._recover_attempts = 0
        self._backend_fallback_done = False
        self.lens = LensCoverDetector()
        self.capture_backend = ""
        self.shared_mode = False
        self.camera_state = "STOPPED"
        self._owner_token = None
        self._owner_key = None
        self._lease_count = 0

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def lens_covered(self) -> bool:
        """Suspected lens cover (dark-flat sustained frames)."""
        try:
            return bool(self.lens.covered)
        except Exception:
            return False

    def _acquire_owner(self) -> bool:
        key = (str(os.name).lower(), int(self.index))
        token = object()
        with _CAMERA_OWNER_LOCK:
            if key in _ACTIVE_CAMERA_OWNERS:
                return False
            _ACTIVE_CAMERA_OWNERS[key] = token
        self._owner_key = key
        self._owner_token = token
        return True

    def _release_owner(self) -> None:
        key, token = self._owner_key, self._owner_token
        self._owner_key = None
        self._owner_token = None
        if key is None:
            return
        with _CAMERA_OWNER_LOCK:
            if _ACTIVE_CAMERA_OWNERS.get(key) is token:
                _ACTIVE_CAMERA_OWNERS.pop(key, None)

    def _choose_backend(self, cv2) -> tuple:
        """Prefer Windows Media Foundation, never silently force DirectShow."""
        msmf = getattr(cv2, "CAP_MSMF", None)
        dshow = getattr(cv2, "CAP_DSHOW", None)
        if (os.name == "nt" and self.backend_preference in {"auto", "msmf"}
                and msmf is not None):
            return msmf, "MSMF", True
        if (self.backend_preference in {"auto", "dshow"}
                and dshow is not None):
            return dshow, "DSHOW", False
        if msmf is not None:
            return msmf, "MSMF", True
        if dshow is not None:
            return dshow, "DSHOW", False
        return None, "", False

    def start(self) -> bool:
        try:
            import cv2
        except Exception as e:
            self.last_error = f"opencv missing: {e}"
            self.camera_state = "DEVICE_UNSUPPORTED_FOR_SHARED_CAPTURE"
            return False
        with self._lock:
            if self._running:
                self._lease_count += 1
                return True
            self.camera_state = "OPENING"
            self.last_error = ""
            if not self._acquire_owner():
                self.camera_state = "TEMPORARILY_UNAVAILABLE"
                self.last_error = (
                    f"camera {self.index} already owned by DUDE")
                return False
            backend_code, backend_name, shared = self._choose_backend(cv2)
            if backend_code is None:
                self._release_owner()
                self.camera_state = "DEVICE_UNSUPPORTED_FOR_SHARED_CAPTURE"
                self.last_error = "no supported OpenCV camera backend"
                return False
            self.capture_backend = backend_name
            self.shared_mode = bool(shared)
            # Camera opens can block for minutes on dead indices. Keep the
            # caller bounded and never start a reopen loop after a failure.
            slot: dict = {}

            def _open():
                try:
                    cap = cv2.VideoCapture(self.index, backend_code)
                    if slot.get("abandoned"):
                        try:
                            cap.release()
                        except Exception:
                            pass
                        return
                    slot["cap"] = cap if cap.isOpened() else None
                    if slot["cap"] is None:
                        try:
                            cap.release()
                        except Exception:
                            pass
                except Exception as e:
                    slot["error"] = str(e)[:200]

            _t = threading.Thread(target=_open, daemon=True)
            _t.start()
            _t.join(timeout=OPEN_TIMEOUT_SECONDS)
            cap = slot.get("cap")
            if cap is None:
                self._release_owner()
                if _t.is_alive():
                    slot["abandoned"] = True
                    self.last_error = f"camera {self.index} open timed out"

                    def _reap_late_open():
                        _t.join(timeout=OPEN_TIMEOUT_SECONDS)
                        late = slot.get("cap")
                        if late is not None:
                            try:
                                late.release()
                            except Exception:
                                pass
                    threading.Thread(
                        target=_reap_late_open, daemon=True).start()
                else:
                    self.last_error = slot.get(
                        "error") or f"camera {self.index} unavailable"
                self.camera_state = "TEMPORARILY_UNAVAILABLE"
                return False
            try:
                self._cap = cap
                self._running = True
                self._read_failures = 0
                self._lease_count = 1
                # Opening a handle is not proof that this device can deliver
                # frames alongside another consumer. Promote to AVAILABLE
                # only after the first real frame arrives.
                self.camera_state = "OPENING"
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)[:200]
                self.camera_state = "TEMPORARILY_UNAVAILABLE"
                self._release_owner()
                return False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        with self._lock:
            if self._lease_count > 1:
                self._lease_count -= 1
                return
            self._lease_count = 0
            self._running = False
            cap, self._cap = getattr(self, "_cap", None), None
        try:
            self.lens.reset()
        except Exception:
            pass
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
        try:
            if self._thread is not None:
                self._thread.join(timeout=3.0)
        except Exception:
            pass
        self._thread = None
        self._release_owner()
        with self._lock:
            if not self._running:
                self.camera_state = "STOPPED"

    def _recover_capture(self, wanted_leases: int = 1) -> bool:
        """Re-open the camera after a run of failed frame reads.

        Mirrors the microphone's self-healing (``no mic frames ...;
        re-opening``).  Retries a bounded number of times with a backoff so a
        genuinely absent device stops retrying instead of hammering the
        driver, and reports a truthful terminal state when it gives up.

        ``wanted_leases`` preserves the outstanding consumers across the
        re-open so a later ``stop()`` cannot leave a dangling lease.
        """
        import time as _t
        # MSMF is preferred on Windows, but it is the flaky one: it can open
        # successfully and then fail every grab with -1072873822, which used to
        # end hand input for the whole session. Re-opening the same backend
        # would just reproduce the fault, so recovery steps down to DirectShow
        # once before spending the remaining attempt budget.
        if (not self._backend_fallback_done
                and self.capture_backend.upper() == "MSMF"):
            self._backend_fallback_done = True
            self.backend_preference = "dshow"
            log.warning(
                "hand camera: MSMF capture failed; recovering with the "
                "DirectShow backend")
        for attempt in range(self.RECOVER_ATTEMPTS):
            # The budget is cumulative, not per call: a successful re-open
            # starts a fresh capture loop which can itself hit read loss and
            # ask to recover again, so a per-call limit would recurse into an
            # unbounded reopen storm against a permanently dead device.
            with self._lock:
                if self._recover_attempts >= self.RECOVER_ATTEMPTS:
                    break
                self._recover_attempts += 1
            _t.sleep(self.RECOVER_BACKOFF_SECONDS * (attempt + 1))
            try:
                if self.start():
                    with self._lock:
                        if wanted_leases > 1:
                            self._lease_count = wanted_leases
                    return True
            except Exception as exc:
                self.last_error = str(exc)[:200]
        with self._lock:
            self._running = False
            self._lease_count = 0
            self._cap = None
            self.camera_state = "UNAVAILABLE"
            self.last_error = self.last_error or "camera unavailable"
        self._release_owner()
        return False

    def _loop(self) -> None:
        import time as _t
        while True:
            with self._lock:
                if not self._running:
                    return
                cap = getattr(self, "_cap", None)
            if cap is None:
                return
            try:
                ok, frame = cap.read()
                if ok and frame is not None:
                    self._read_failures = 0
                    # Frames are flowing again: the reopen budget is restored
                    # so a later, unrelated hiccup can still self-heal.
                    self._recover_attempts = 0
                    if self.camera_state not in ("AVAILABLE",
                                                "AVAILABLE_SHARED"):
                        self.camera_state = (
                            "AVAILABLE_SHARED" if self.shared_mode
                            else "AVAILABLE")
                        log.info(
                            "HANDCAM camera live backend=%s frames=%d",
                            self.capture_backend or "?", self.frames_seen + 1)
                    try:
                        # Thumbnail stats only (no storage): sustained
                        # dark-flat frames mean a covered lens.
                        import cv2 as _cv
                        tiny = _cv.resize(frame, (32, 32))
                        gray = _cv.cvtColor(tiny, _cv.COLOR_BGR2GRAY)
                        _m, _s = _cv.meanStdDev(gray)
                        self.lens.update(float(_m[0][0]), float(_s[0][0]))
                    except Exception:
                        pass
                    with self._lock:
                        self._frame = frame
                        self._frame_id += 1
                        self.frames_seen += 1
                        self.camera_state = (
                            "AVAILABLE_SHARED" if self.shared_mode
                            else "AVAILABLE")
                else:
                    self._read_failures += 1
                    if self._read_failures >= self.READ_FAILURE_LIMIT:
                        with self._lock:
                            wanted = self._lease_count
                            self._running = False
                            self._cap = None
                            self.camera_state = "RECOVERING"
                            self.last_error = "camera frames unavailable"
                        try:
                            cap.release()
                        except Exception:
                            pass
                        self._release_owner()
                        # A transient capture failure must not end hand input
                        # for the whole session. The microphone already
                        # re-opens itself; the camera now does the same, so a
                        # single MSMF hiccup no longer silently kills every
                        # gesture until DUDE is restarted by hand.
                        if wanted:
                            self._recover_capture(wanted)
                        else:
                            with self._lock:
                                self.camera_state = "STOPPED"
                        return
            except Exception as e:
                self.last_error = str(e)[:200]
                self._read_failures += 1
                if self._read_failures >= self.READ_FAILURE_LIMIT:
                    with self._lock:
                        wanted = self._lease_count
                        self._running = False
                        self._cap = None
                        self.camera_state = "RECOVERING"
                    try:
                        cap.release()
                    except Exception:
                        pass
                    self._release_owner()
                    if wanted:
                        self._recover_capture(wanted)
                    else:
                        with self._lock:
                            self.camera_state = "STOPPED"
                    return
            _t.sleep(self.interval)

    def latest(self):
        """Newest frame + id (no history; same array object may repeat)."""
        with self._lock:
            return self._frame, self._frame_id

    def status(self) -> dict:
        return {
            "state": self.camera_state,
            "capture_backend": self.capture_backend,
            "capture_mechanism": (
                "Windows Media Foundation" if self.capture_backend == "MSMF"
                else self.capture_backend or "none"),
            "sharing_policy": "application-agnostic",
            "shared_mode": bool(self.shared_mode),
            "frames_seen": int(self.frames_seen),
            "leases": int(self._lease_count),
            "last_error": self.last_error[:120],
        }


class HandPerception:
    """Frame -> HandState via the local landmark model."""

    def __init__(self, model_path: str = MODEL_PATH,
                 min_conf: float = MIN_SIGNAL_SCORE):
        self.model_path = model_path
        self.min_conf = min_conf
        self._landmarker = None
        self.last_error = ""
        self.frames_processed = 0
        self.identity_tracker = HandIdentityTracker()

    @property
    def available(self) -> bool:
        return self._landmarker is not None

    def close(self) -> None:
        """Release the model (native threads can wedge process exit)."""
        try:
            lm, self._landmarker = self._landmarker, None
            if lm is not None:
                try:
                    lm.close()
                except Exception:
                    pass
        except Exception:
            pass

    def load(self) -> bool:
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision
            base = mp_python.BaseOptions(model_asset_path=self.model_path)
            opts = mp_vision.HandLandmarkerOptions(
                base_options=base, num_hands=MAX_HANDS,
                min_hand_detection_confidence=self.min_conf,
                min_hand_presence_confidence=self.min_conf,
                min_tracking_confidence=self.min_conf)
            self._landmarker = mp_vision.HandLandmarker.create_from_options(
                opts)
            return True
        except Exception as e:
            self.last_error = str(e)[:200]
            log.warning(f"hand model load failed: {e}")
            return False

    def state_from_landmarks(self, hands_data) -> HandState:
        """Pure construction from landmark lists (testable, no model)."""
        obs = []
        try:
            for item in (hands_data or [])[:MAX_HANDS]:
                lm, label, conf = item
                pts3 = [
                    (float(p[0]), float(p[1]),
                     float(p[2]) if len(p) > 2 else 0.0)
                    for p in lm[:21]
                ]
                pts = [(p[0], p[1]) for p in pts3]
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                gesture, gconf = classify_gesture(pts)
                try:
                    palm = tuple(sum(v) / len(v) for v in zip(*pts[1:]))
                except Exception:
                    palm = ()
                normalized_label = str(label or "unknown")[:16].lower()
                identity = (
                    normalized_label if normalized_label in {"left", "right"}
                    else f"hand_{len(obs)}")
                obs.append(HandObservation(
                    label=normalized_label,
                    identity=identity,
                    wrist=tuple(pts[0]),
                    palm=palm,
                    bbox=(min(xs), min(ys), max(xs) - min(xs),
                          max(ys) - min(ys)),
                    landmarks=tuple(pts),
                    landmarks_3d=tuple(pts3),
                    handedness_score=float(conf),
                    gesture=gesture,
                    gesture_confidence=float(gconf),
                    captured_at=time.time()))
        except Exception as e:
            log.warning(f"state_from_landmarks failed: {e}")
        return HandState(
            hands=self.identity_tracker.assign(obs),
            captured_at=time.time())

    def process_frame(self, bgr) -> Optional[HandState]:
        """One BGR frame -> HandState (None when unavailable/empty)."""
        try:
            if self._landmarker is None or bgr is None:
                return None
            import cv2
            import mediapipe as mp
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            mpi = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            res = self._landmarker.detect(mpi)
            self.frames_processed += 1
            if not res.hand_landmarks:
                return HandState(hands=(), captured_at=time.time())
            items = []
            labels = []
            scores = []
            try:
                for hcat in (res.handedness or []):
                    try:
                        labels.append(hcat[0].category_name)
                        scores.append(float(hcat[0].score))
                    except Exception:
                        labels.append("unknown")
                        scores.append(0.0)
            except Exception:
                labels = []
                scores = []
            for i, hand in enumerate(res.hand_landmarks[:MAX_HANDS]):
                try:
                    # Handedness classifier score: the only per-hand
                    # numeric the Tasks API exposes. Recorded as signal
                    # quality, never as detector confidence (unexposed).
                    # Unreadable score fails closed to 0.
                    try:
                        conf = float(scores[i]) if i < len(scores) \
                            else 0.0
                    except Exception:
                        conf = 0.0
                    pts3 = [(float(p.x), float(p.y), float(p.z))
                            for p in hand]
                    label = (labels[i] if i < len(labels) else "unknown")
                    items.append((pts3, label, conf))
                except Exception:
                    continue
            return self.state_from_landmarks(items)
        except Exception as e:
            self.last_error = str(e)[:200]
            return None


class HandScreenMapper:
    """Camera-normalized space -> screen pixels (params only)."""

    def __init__(self, mirror: bool = True, edge_margin: float = 0.015):
        self.mirror = mirror
        self.edge_margin = max(0.0, min(0.08, float(edge_margin)))
        self._lock = threading.RLock()
        # Calibration: affine ((a, b, c), (d, e, f)) + display rect it
        # was taken on. Default = fullscreen stretch (uncalibrated).
        self._affine = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        self._calib_display: tuple = ()
        self.calibrated = False

    def display_rect(self) -> tuple:
        try:
            import ctypes
            u = ctypes.windll.user32
            return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
                    u.GetSystemMetrics(78), u.GetSystemMetrics(79))
        except Exception:
            return (0, 0, 1920, 1080)

    def calibrate(self, corners) -> bool:
        """Fit affine from 4 camera-normalized corners to display px.

        corners: [(nx, ny, sx, sy)] * 4. Stores params + display rect.
        """
        try:
            import numpy as np
            if not corners or len(corners) < 4:
                return False
            A, B = [], []
            for nx, ny, sx, sy in corners[:4]:
                A.append([nx, ny, 1, 0, 0, 0])
                A.append([0, 0, 0, nx, ny, 1])
                B += [sx, sy]
            p, _, _, _ = __import__("numpy").linalg.lstsq(
                np.array(A, float), np.array(B, float), rcond=None)
            with self._lock:
                self._affine = ((p[0], p[1], p[2]), (p[3], p[4], p[5]))
                self._calib_display = self.display_rect()
                self.calibrated = True
            return True
        except Exception as e:
            log.warning(f"calibration failed: {e}")
            return False

    def calibration_live(self) -> bool:
        """False when display geometry moved since calibration."""
        try:
            with self._lock:
                if not self.calibrated:
                    return False
                return tuple(self._calib_display) == tuple(
                    self.display_rect())
        except Exception:
            return False

    def map(self, nx: float, ny: float) -> tuple:
        """Normalized camera point -> screen pixels (mirrored default)."""
        try:
            if self.mirror:
                nx = 1.0 - nx
            with self._lock:
                (a, b, c), (d, e, f) = self._affine
                edge_margin = self.edge_margin
            if not (self.calibrated and self.calibration_live()):
                nx = edge_margin + nx * (1.0 - 2.0 * edge_margin)
                ny = edge_margin + ny * (1.0 - 2.0 * edge_margin)
            vx, vy, vw, vh = self.display_rect()
            if self.calibrated and self.calibration_live():
                return (a * nx + b * ny + c, d * nx + e * ny + f)
            return (vx + nx * vw, vy + ny * vh)
        except Exception:
            return ()


def _foreground_is_own_window() -> bool:
    """True when DUDE's own overlay currently owns the foreground.

    The Command Centre / notch / pointer overlay are genuine top-level
    windows, so ``GetForegroundWindow`` can legitimately return one of them.
    The visual state is built from the foreground window, so in that case the
    state describes DUDE's own UI and must not ground a hand click.
    """
    try:
        import os

        import win32gui
        import win32process
        hwnd = win32gui.GetForegroundWindow() or 0
        if not hwnd:
            return False
        return int(win32process.GetWindowThreadProcessId(hwnd)[1]) \
            == int(os.getpid())
    except Exception:
        return False


#: Window classes that are the Windows desktop surface rather than an
#: application. A hand gesture must never be delivered to the wallpaper.
_DESKTOP_SHELL_CLASSES = {
    "Progman", "WorkerW", "SHELLDLL_DefView", "DesktopWindowXamlSource",
    "Windows.UI.Core.CoreWindow",
}


#: Element/window names the Windows shell reports for the desktop surface.
_DESKTOP_SHELL_NAMES = {
    "desktopwindowxamlsource", "progman", "workerw", "program manager",
    "shelldll_defview", "desktop", "windows.ui.core.corewindow", "workerw",
}


def _target_is_desktop_shell(target) -> bool:
    """True when a grounded target is the desktop, not an application.

    Applied to whatever the visual bus resolved, not only to the window-under-
    point fallback: the shell can be named by UIA or OCR too, and a click
    aimed at the wallpaper is accepted by the engine and then never executes.
    """
    try:
        if not target:
            return False
        window = target.get("window") or ()
        candidates = [
            str(target.get("element", "") or ""),
            str(window[0] if len(window) > 0 else ""),
            str(window[1] if len(window) > 1 else ""),
            str(target.get("provenance", "") or ""),
        ]
        for raw in candidates:
            if str(raw).strip().lower() in _DESKTOP_SHELL_NAMES:
                return True
        return False
    except Exception:
        return False


def _is_own_hwnd(hwnd) -> bool:
    """True when a window handle belongs to this DUDE process.

    DUDE's own panels (Command Centre, notch, desktop pointer overlay) are
    real top-level windows.  They must never be the ground truth for a
    hand-driven click, otherwise DUDE's chrome hijacks every gesture.
    """
    try:
        import os

        import win32process
        if not hwnd:
            return False
        return int(win32process.GetWindowThreadProcessId(int(hwnd))[1]) \
            == int(os.getpid())
    except Exception:
        return False


def _window_under_point(screen_xy) -> Optional[dict]:
    """Resolve the real top-level application window beneath a screen point.

    This is the desktop-wide counterpart to ``relate_to_visual``: the visual
    state only ever describes ONE window (the foreground one), so a fingertip
    over any other application would otherwise ground to nothing at all.

    ``WindowFromPoint`` returns the deepest child widget, so the handle is
    lifted to its top-level ancestor first — otherwise a click over Notepad
    grounds on an anonymous inner control instead of the app.  If that top
    level window belongs to DUDE, the point is over DUDE's own chrome and no
    target is returned, so a gesture can never retarget its own UI.
    """
    try:
        import win32con
        import win32gui

        x, y = int(round(float(screen_xy[0]))), int(round(float(screen_xy[1])))
        hwnd = win32gui.WindowFromPoint((x, y))
        if not hwnd:
            return None
        try:
            root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd
        except Exception:
            root = hwnd
        if not root or _is_own_hwnd(root):
            return None
        # The desktop wallpaper is a real top-level window (Progman / WorkerW
        # / DesktopWindowXamlSource). Treating it as a click target meant a
        # gesture over bare desktop was delivered to the background and
        # appeared to do nothing, while masking the fact that no application
        # was under the fingertip. It is never a valid hand target.
        try:
            root_class = str(win32gui.GetClassName(root) or "")
        except Exception:
            root_class = ""
        if root_class in _DESKTOP_SHELL_CLASSES:
            return None
        try:
            title = win32gui.GetWindowText(root) or ""
        except Exception:
            title = ""
        if root_class in _DESKTOP_SHELL_CLASSES or str(title) in (
                "Program Manager", "Desktop", "WorkerW"):
            return None
        if not title:
            try:
                title = win32gui.GetClassName(root) or ""
            except Exception:
                title = ""
        try:
            left, top, right, bottom = win32gui.GetWindowRect(root)
            rect = (int(left), int(top), int(right - left), int(bottom - top))
        except Exception:
            rect = ()
        if rect and rect[2] <= 0 or rect and rect[3] <= 0:
            return None
        return {"element": str(title or "window")[:60], "kind": "window",
                "rect": rect, "window": (str(root), str(title)),
                "window_rect": rect,
                "provenance": "hand+point:window_under_point"}
    except Exception as exc:
        log.debug("window_under_point failed: %s", exc)
        return None


def relate_to_visual(screen_xy, visual_bus) -> Optional[dict]:
    """Hand screen point -> UI element under it (current evidence only).

    Returns None for stale visual state, misses, or (by design) always
    an observation dict — never an action.
    """
    try:
        if not screen_xy or len(screen_xy) != 2:
            return None
        state = None
        try:
            state = visual_bus.get_fresh()
        except Exception:
            state = None
        if state is None:
            # No fresh-state API or nothing fresh: fall back to the
            # current state but enforce the bus's own staleness bar.
            try:
                state = visual_bus.get()
            except Exception:
                state = None
        if state is None:
            # No visual state at all. The desktop still has a real window
            # under the fingertip, so resolve it directly instead of dropping
            # the gesture. Hand actions must not depend on the Holo/Command
            # Centre surface being present or fresh.
            return _window_under_point(screen_xy)
        try:
            ttl = getattr(visual_bus, "stale_after_seconds", None)
            if ttl is None:
                ttl = getattr(visual_bus, "ttl_seconds", 5.0)
            if state.is_stale(float(ttl)):
                return _window_under_point(screen_xy)
        except Exception:
            pass
        x, y = float(screen_xy[0]), float(screen_xy[1])
        window_bounds = getattr(state, "window_bounds", None)
        window_rect = ()
        if window_bounds is not None:
            try:
                if len(window_bounds) >= 4:
                    window_rect = tuple(int(v) for v in window_bounds[:4])
                else:
                    window_rect = (
                        int(getattr(window_bounds, "x", 0) or 0),
                        int(getattr(window_bounds, "y", 0) or 0),
                        int(getattr(window_bounds, "w", 0) or 0),
                        int(getattr(window_bounds, "h", 0) or 0),
                    )
            except Exception:
                window_rect = ()
        # DUDE's own panels are real always-on-top top-level windows.  When
        # one of them owns the foreground the visual state describes ONLY
        # DUDE's own chrome, so grounding against it makes every gesture
        # retarget the Command Centre instead of the app under the fingertip.
        # Skip the visual state entirely in that case and resolve the window
        # that is genuinely beneath the hand.
        if _foreground_is_own_window():
            return _window_under_point(screen_xy)
        names = list(getattr(state, "control_names", "") or [])
        rects = list(getattr(state, "control_rects", "") or [])
        for i, raw in enumerate(names):
            r = rects[i] if i < len(rects) else ()
            try:
                if len(r) == 4 and r[0] <= x <= r[0] + r[2] \
                        and r[1] <= y <= r[1] + r[3]:
                    return {"element": str(raw)[:60], "kind": "uia",
                            "rect": tuple(r),
                            "window": (getattr(state, "active_app", "?"),
                                       getattr(state, "window_title", "")),
                            "window_rect": window_rect,
                            "provenance": f"hand+uia:{str(raw)[:40]}"}
            except Exception:
                continue
        for item in (getattr(state, "ocr_items", "") or []):
            try:
                text, r = item[0], item[1]
                if len(r) == 4 and r[0] <= x <= r[0] + r[2] \
                        and r[1] <= y <= r[1] + r[3]:
                    return {"element": str(text)[:60], "kind": "ocr",
                            "rect": tuple(r),
                            "window": (getattr(state, "active_app", "?"),
                                       getattr(state, "window_title", "")),
                            "window_rect": window_rect,
                            "provenance": f"hand+ocr:{str(text)[:40]}"}
            except Exception:
                continue
        # Coarse fallback: the containing window is still a real UI
        # region (mirrors the spatial workspace's focus resolution).
        try:
            wb = getattr(state, "window_bounds", "") or ()
            if len(wb) == 4 and wb[0] <= x <= wb[0] + wb[2] \
                    and wb[1] <= y <= wb[1] + wb[3]:
                app = getattr(state, "active_app", "?")
                title = getattr(state, "window_title", "")
                return {"element": str(title or app)[:60], "kind": "window",
                        "rect": tuple(wb), "window": (app, title),
                        "window_rect": window_rect,
                        "provenance": "hand+window:active"}
        except Exception:
            pass
        # Last resort: the fingertip landed outside the single window the
        # visual state happens to describe.  The desktop still has a real
        # window under that point, so resolve it instead of dropping the
        # gesture on the floor.
        return _window_under_point(screen_xy)
    except Exception as e:
        log.warning(f"relate_to_visual failed: {e}")
        return None


class HandTracker:
    """Camera lifecycle + perception + stable state (the capability)."""

    def __init__(self, bus=None, camera_index: int = 0,
                 fps: float = 10.0):
        self.bus = bus or HandStateBus()
        self.camera = get_hand_camera(index=camera_index, fps=fps)
        self.perception = HandPerception()
        self.stabilizer = HandStabilizer()
        self.stabilizers: dict = {}
        self.mapper = HandScreenMapper()
        self.interaction_mapper = HandInteractionMapper()
        self._action_sink = None
        self._visual_bus = None
        self._action_feedback_source = None
        self.spatial_layer = SpatialInteractionLayer()
        self._lock = threading.Lock()
        self._loop_on = False
        self._starting = False
        self._thread: Optional[threading.Thread] = None
        self._stable = (Gesture.UNKNOWN, 0)
        self._stable_by_hand: dict = {}
        self._last_interaction: Optional[HandInteraction] = None
        self._last_action_signature = None
        self._last_frame_id = -1
        self.last_error = ""

    def set_action_sink(self, sink) -> None:
        """Attach a semantic proposal sink; it cannot execute directly."""
        with self._lock:
            self._action_sink = sink

    def set_visual_bus(self, visual_bus) -> None:
        """Attach the existing visual truth used to ground semantic targets."""
        with self._lock:
            self._visual_bus = visual_bus

    def set_action_feedback_source(self, source) -> None:
        """Attach the existing TaskEngine feedback reader for the UI overlay."""
        with self._lock:
            self._action_feedback_source = source

    def action_feedback(self) -> dict:
        source = None
        with self._lock:
            source = self._action_feedback_source
        try:
            reader = getattr(source, "hand_action_feedback", None)
            return dict(reader() or {}) if callable(reader) else {}
        except Exception:
            return {}

    def set_spatial_layer(self, spatial_layer) -> None:
        """Attach the virtual-object hit-test/manipulation layer."""
        if spatial_layer is None:
            return
        with self._lock:
            self.spatial_layer = spatial_layer

    def spatial_snapshot(self) -> dict:
        try:
            return self.spatial_layer.snapshot()
        except Exception:
            return {}

    @staticmethod
    def _suppress_spatial_desktop_action(interaction: HandInteraction,
                                         spatial_snapshot: dict
                                         ) -> HandInteraction:
        """Keep virtual-object gestures out of the desktop action bridge."""
        if not getattr(interaction, "action_type", ""):
            return interaction
        spatial_ids = {
            str(hand.get("hand_id", ""))
            for hand in (spatial_snapshot.get("hands") or ())
            if isinstance(hand, dict)
            and (hand.get("grabbed") or hand.get("target_id"))
        }
        if not spatial_ids.intersection(
                str(hand_id) for hand_id in
                (getattr(interaction, "hand_ids", ()) or ())):
            return interaction
        return replace(
            interaction,
            action_type="",
            action_parameters={},
            target={},
            target_description="spatial interaction active",
        )

    def _prefer_non_spatial_interaction(self, state: HandState,
                                        interaction: HandInteraction,
                                        stable_by_hand: dict,
                                        spatial_snapshot: dict,
                                        timestamp: float
                                        ) -> HandInteraction:
        """Keep an independent screen hand clickable beside a spatial hand."""
        spatial_ids = {
            str(hand.get("hand_id", ""))
            for hand in (spatial_snapshot.get("hands") or ())
            if isinstance(hand, dict)
            and (hand.get("grabbed") or hand.get("target_id"))
        }
        interaction_ids = {
            str(hand_id) for hand_id in
            (getattr(interaction, "hand_ids", ()) or ())
        }
        two_hand_scale = (
            interaction.name == SemanticInteraction.TWO_HAND_SCALE
            or interaction.action_type == "resize_window")
        if not spatial_ids and not two_hand_scale:
            return interaction
        if (interaction.action_type
                and not two_hand_scale
                and not interaction_ids.intersection(spatial_ids)):
            return interaction

        candidates = []
        points = {}
        for index, hand in enumerate(getattr(state, "hands", ()) or ()):
            hand_id = self.interaction_mapper._identity(hand, index)
            if hand_id in spatial_ids:
                continue
            gesture = stable_by_hand.get(
                hand_id, (getattr(hand, "gesture", Gesture.UNKNOWN), 0))[0]
            points[hand_id] = self.interaction_mapper._point_for(
                hand, gesture, hand_id)
            single_state = HandState(
                hands=(hand,), frame_id=int(getattr(state, "frame_id", 0) or 0))
            candidate = self.interaction_mapper._observe_one_hand(
                single_state, (hand,), stable_by_hand, points, timestamp)
            if candidate.action_type:
                candidates.append(self._ground_interaction(candidate))
        targeted = [candidate for candidate in candidates
                    if getattr(candidate, "target", {})]
        if len(targeted) == 1:
            return targeted[0]
        specific = [candidate for candidate in targeted
                    if getattr(candidate, "target", {}).get("kind")
                    in ("uia", "ocr")]
        if len(specific) == 1:
            return specific[0]
        if spatial_ids and len(candidates) == 1:
            return candidates[0]
        return interaction

    def _ground_interaction(self, interaction: HandInteraction
                            ) -> HandInteraction:
        if not interaction.action_type or not interaction.point:
            return interaction
        with self._lock:
            visual_bus = self._visual_bus
        if visual_bus is None:
            # Grounding must not require the visual bus. Resolve the window
            # under the fingertip directly so OS-level actions work
            # independently of any DUDE panel.
            try:
                target = _window_under_point(
                    self.mapper.map(*interaction.point))
                if target:
                    return interaction.with_target(
                        target,
                        screen_point=self.mapper.map(*interaction.point))
            except Exception:
                pass
            return interaction
        try:
            screen_xy = None
            # The desktop overlay and executable action must use the same
            # point. The spatial layer already smooths the active fingertip;
            # do not re-project the raw camera point through a second filter.
            #
            # Use the spatial layer's pointer for the hand that owns this
            # interaction. Previously this was restricted to a single hand, so
            # with BOTH hands visible every gesture fell back to one shared
            # mapper point that belonged to neither dot - which is why neither
            # dot could click. Each hand now resolves its own pointer.
            hand_ids = list(getattr(interaction, "hand_ids", ()) or ())
            snapshot = self.spatial_layer.snapshot()
            for pointer in (snapshot.get("pointers") or ()):
                pid = str(pointer.get("hand_id", ""))
                if pointer.get("screen_xy") is None:
                    continue
                if hand_ids and pid not in {str(h) for h in hand_ids}:
                    continue
                screen_xy = tuple(pointer["screen_xy"])
                break
            if not screen_xy:
                screen_xy = self.mapper.map(*interaction.point)
            # A drag carries camera-normalised endpoints, but the executor
            # sends them to the OS as pixels. Convert them here, with the same
            # mapper instance that produced screen_xy, so a drag lands where
            # the hand actually is instead of in the top-left corner.
            if str(getattr(interaction, "action_type", "") or "") == "drag":
                params = getattr(interaction, "action_parameters", None)
                if isinstance(params, dict) and "x0" in params:
                    try:
                        x0, y0 = self.mapper.map(
                            float(params["x0"]), float(params["y0"]))
                        x1, y1 = self.mapper.map(
                            float(params["x1"]), float(params["y1"]))
                        if len(x0) == 2 and len(x1) == 2:
                            params["x0"], params["y0"] = float(x0), float(y0)
                            params["x1"], params["y1"] = float(x1), float(y1)
                    except Exception:
                        pass
            target = relate_to_visual(screen_xy, visual_bus)
            if _target_is_desktop_shell(target):
                # The visual bus described the desktop itself (wallpaper /
                # shell), not an application. Dispatching here produced clicks
                # that were accepted and then silently never executed. Try the
                # real window under the fingertip instead, and if there is no
                # application there, drop the action honestly.
                resolved = _window_under_point(screen_xy)
                if _target_is_desktop_shell(resolved):
                    log.info(
                        "hand gesture over bare desktop, no application under "
                        "the cursor: dropping %s",
                        getattr(interaction, "action_type", "?"))
                    return interaction
                target = resolved
            return interaction.with_target(target, screen_point=screen_xy) \
                if target else interaction
        except Exception as exc:
            log.warning("hand semantic target grounding failed: %s", exc)
            return interaction

    def stable_by_hand(self) -> dict:
        with self._lock:
            return dict(self._stable_by_hand)

    def interaction(self) -> Optional[HandInteraction]:
        with self._lock:
            return self._last_interaction

    def start_camera(self, index: Optional[int] = None) -> bool:
        try:
            if index is not None:
                with self._lock:
                    if self.camera.running:
                        return False
                    self.camera = HandCamera(
                        index=index, fps=1.0 / self.camera.interval)
            if not self.perception.available:
                self.perception.load()
            if not self.camera.start():
                self.last_error = self.camera.last_error
                return False
            with self._lock:
                if self._loop_on:
                    return True
                self._loop_on = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            self.last_error = str(e)[:200]
            return False

    def start_camera_async(self, index: Optional[int] = None) -> bool:
        """Launch camera opening/loading on a daemon worker.

        The caller never waits on hardware: model load plus the
        bounded camera open both happen off-thread and report through
        last_error/status. Duplicate launches collapse into one
        (already running or already starting -> False).
        """
        try:
            with self._lock:
                if self.camera.running or self._starting:
                    return False
                self._starting = True

            def _boot(idx=index):
                try:
                    log.info("HANDCAM boot requested index=%s", idx)
                    ok = self.start_camera(idx)
                    log.info(
                        "HANDCAM boot result ok=%s state=%s backend=%s "
                        "err=%s", ok, self.camera.camera_state,
                        self.camera.capture_backend or "?", self.last_error
                        or "-")
                    if not ok:
                        log.warning("hand camera boot failed: %s",
                                    self.last_error)
                except Exception as e:
                    try:
                        self.last_error = str(e)[:200]
                    except Exception:
                        pass
                    log.warning(f"hand camera boot crashed: {e}")
                finally:
                    try:
                        with self._lock:
                            self._starting = False
                    except Exception:
                        pass

            threading.Thread(target=_boot, daemon=True).start()
            return True
        except Exception as e:
            try:
                with self._lock:
                    self._starting = False
            except Exception:
                pass
            self.last_error = str(e)[:200]
            return False

    def stop_camera(self) -> None:
        with self._lock:
            self._loop_on = False
        try:
            if self._thread is not None:
                self._thread.join(timeout=3.0)
        except Exception:
            pass
        self._thread = None
        try:
            self.camera.stop()
        except Exception:
            pass
        try:
            self.stabilizer.reset()
        except Exception:
            pass
        with self._lock:
            self._stable = (Gesture.UNKNOWN, 0)
            self._stable_by_hand.clear()
            self._last_interaction = None
            self._last_action_signature = None
        try:
            for stabilizer in self.stabilizers.values():
                stabilizer.reset()
            self.stabilizers.clear()
            self.interaction_mapper = HandInteractionMapper()
            self.spatial_layer.clear_hands()
            self.bus.publish(HandState(hands=()), source="hand_tracker_stopped")
        except Exception:
            pass

    def shutdown(self) -> None:
        """Full release: loop, camera, and model (exit-safe)."""
        try:
            self.stop_camera()
        except Exception:
            pass
        try:
            self.perception.close()
        except Exception:
            pass

    @property
    def camera_on(self) -> bool:
        try:
            return self.camera.running
        except Exception:
            return False

    def _loop(self) -> None:
        import time as _t
        while True:
            with self._lock:
                if not self._loop_on:
                    return
            try:
                self.tick()
            except Exception as e:
                self.last_error = str(e)[:200]
            _t.sleep(self.camera.interval)

    def _log_hand_heartbeat(self, state, stable_by_hand, interaction) -> None:
        """One compact line per second describing what the hand layer sees.

        Without this there is no evidence at all when gestures "just don't
        detect": the panel shows a number but the log stays silent, so a dead
        camera, a rejected detection score and a mislabelled pose are
        indistinguishable. Reports the camera backend, the per-hand raw
        score, the stabilised gesture with its vote count, and the resulting
        interaction, so the failing stage is always identifiable.
        """
        try:
            now = time.time()
            last = getattr(self, "_last_heartbeat_at", 0.0)
            if now - last < 1.0:
                return
            self._last_heartbeat_at = now
            cam = self.camera
            hands = []
            for index, h in enumerate(getattr(state, "hands", ()) or ()):
                try:
                    ident = str(getattr(h, "identity", "")
                                or getattr(h, "label", "")
                                or f"hand_{index}").strip().lower()
                    stable, votes = stable_by_hand.get(
                        ident, (Gesture.UNKNOWN, 0))
                    hands.append(
                        "%s:raw=%s/score=%.2f->%s/%d" % (
                            ident,
                            str(getattr(getattr(h, "gesture", Gesture.UNKNOWN),
                                        "value", getattr(h, "gesture", "?"))),
                            float(getattr(h, "handedness_score", 0.0) or 0.0),
                            str(getattr(stable, "value", stable)),
                            int(votes or 0)))
                except Exception:
                    continue
            log.info(
                "HANDHB cam=%s/%s frames=%s hands=%d [%s] act=%s tgt=%s",
                getattr(cam, "capture_backend", "?") or "?",
                getattr(cam, "camera_state", "?"),
                getattr(cam, "frames_seen", 0),
                len(getattr(state, "hands", ()) or ()),
                " ".join(hands) or "-",
                str(getattr(interaction, "name", "") or "-"),
                str((getattr(interaction, "target", {}) or {}).get("element", "-")
                    or "-"))
        except Exception:
            pass

    def tick(self) -> Optional[HandState]:
        """One sense cycle: frame -> state -> stabilize -> publish."""
        try:
            frame, fid = self.camera.latest()
            if frame is None or fid == self._last_frame_id:
                return None
            self._last_frame_id = fid
            state = self.perception.process_frame(frame)
            if state is None:
                return None
            try:
                state.frame_id = fid
            except Exception:
                pass
            votes = []
            stable_by_hand = {}
            for index, h in enumerate(state.hands):
                try:
                    identity = getattr(h, "identity", "") or getattr(
                        h, "label", "") or f"hand_{index}"
                    identity = str(identity).strip().lower()
                    if not identity:
                        identity = f"hand_{index}"
                    stabilizer = self.stabilizers.setdefault(
                        identity, HandStabilizer())
                    if h.handedness_score >= MIN_SIGNAL_SCORE:
                        stable_by_hand[identity] = stabilizer.update(h.gesture)
                        votes.append(h.gesture)
                    else:
                        stable_by_hand[identity] = (Gesture.UNKNOWN, 0)
                except Exception:
                    continue
            with self._lock:
                self._stable_by_hand = dict(stable_by_hand)
                # Do not select a primary hand when two are visible.  The
                # legacy scalar is populated only for the genuinely
                # single-hand case; all interaction code consumes the full
                # per-hand map.
                if len(stable_by_hand) == 1:
                    self._stable = next(iter(stable_by_hand.values()))
                else:
                    self._stable = (Gesture.UNKNOWN, 0)
            self.spatial_layer.update(
                state, stable_by_hand=stable_by_hand,
                screen_mapper=self.mapper)
            self.bus.publish(state, source="hand_tracker")
            interaction_timestamp = time.time()
            interaction = self.interaction_mapper.observe(
                state, stable_by_hand=stable_by_hand,
                now=interaction_timestamp)
            spatial_snapshot = self.spatial_layer.snapshot()
            interaction = self._prefer_non_spatial_interaction(
                state, interaction, stable_by_hand, spatial_snapshot,
                interaction_timestamp)
            interaction = self._suppress_spatial_desktop_action(
                interaction, spatial_snapshot)
            interaction = self._ground_interaction(interaction)
            self._log_hand_heartbeat(state, stable_by_hand, interaction)
            with self._lock:
                self._last_interaction = interaction
                sink = self._action_sink
                if not interaction.action_type:
                    self._last_action_signature = None
                else:
                    signature = (
                        interaction.name.value,
                        tuple(interaction.hand_ids),
                        interaction.action_type,
                        tuple(interaction.point or ()),
                    )
                    duplicate = signature == self._last_action_signature
                    self._last_action_signature = signature
            if (callable(sink) and interaction.action_type
                    and not duplicate):
                try:
                    accepted = sink(interaction)
                    if accepted is False:
                        self.interaction_mapper.retry_failed(interaction)
                        with self._lock:
                            self._last_action_signature = None
                except Exception as exc:
                    self.interaction_mapper.retry_failed(interaction)
                    with self._lock:
                        self._last_action_signature = None
                    self.last_error = f"hand action proposal failed: {exc}"[:200]
            return state
        except Exception as e:
            self.last_error = str(e)[:200]
            return None

    def stable(self) -> tuple:
        try:
            return tuple(self._stable)
        except Exception:
            return (Gesture.UNKNOWN, 0)

    def pointed_element(self, visual_bus) -> Optional[dict]:
        """Stable high-confidence pointing + fresh visual hit.

        Observation only. Returns None for low confidence, unstable
        gesture, stale states, or misses — never an action.
        """
        try:
            if not self.camera_on:
                return None
            gesture, votes = self.stable()
            if gesture != Gesture.POINTING or votes < STABLE_NEED:
                return None
            hand = self.bus.get_fresh()
            if hand is None or hand.two_hands():
                # A two-hand frame is never reduced to an arbitrary primary
                # target by this legacy observation helper.
                return None
            prim = hand.hands[0] if hand.hands else None
            if prim is None:
                return None
            if prim.handedness_score < MIN_SIGNAL_SCORE \
                    or prim.gesture_confidence < MIN_GESTURE_CONF:
                return None
            try:
                tip = prim.landmarks[8]
            except Exception:
                return None
            screen_xy = self.mapper.map(tip[0], tip[1])
            if not screen_xy:
                return None
            rel = relate_to_visual(screen_xy, visual_bus)
            if rel is None:
                return None
            rel["gesture"] = "pointing"
            rel["votes"] = votes
            return rel
        except Exception as e:
            log.warning(f"pointed_element failed: {e}")
            return None

    def status(self) -> dict:
        """Real capability state for the existing runtime capability model."""
        try:
            camera_status = self.camera.status()
            fresh = None
            try:
                fresh = self.bus.get_fresh()
            except Exception:
                fresh = None
            two_hand = bool(fresh is not None and fresh.two_hands())
            hand_details = []
            if fresh is not None:
                stable = self.stable_by_hand()
                for hand in fresh.hands:
                    identity = self.interaction_mapper._identity(hand, 0)
                    gesture, votes = stable.get(
                        identity, (getattr(hand, "gesture", Gesture.UNKNOWN), 0))
                    hand_details.append({
                        "identity": identity,
                        "label": str(getattr(hand, "label", "unknown")),
                        "gesture": self.interaction_mapper._gesture_value(gesture),
                        "votes": int(votes or 0),
                        "gesture_confidence": round(float(
                            getattr(hand, "gesture_confidence", 0.0)), 3),
                        "pinch_distance": (
                            round(float(hand.pinch_distance), 5)
                            if hand.pinch_distance is not None else None),
                        "fingertips": {
                            name: [round(float(value), 4) for value in point]
                            for name, point in hand.fingertips.items()
                        },
                    })
            interaction = None
            try:
                current = self.interaction()
                interaction = current.name.value if current is not None else None
            except Exception:
                interaction = None
            common = {
                "camera_state": camera_status.get("state", "STOPPED"),
                "capture_backend": camera_status.get("capture_backend", ""),
                "capture_mechanism": camera_status.get(
                    "capture_mechanism", "none"),
                "sharing_policy": camera_status.get(
                    "sharing_policy", "application-agnostic"),
                "shared_mode": bool(camera_status.get("shared_mode", False)),
                "hand_tracking": bool(self.perception.available),
                "two_hand_tracking": two_hand,
                "hands_detected": int(fresh.count() if fresh is not None else 0),
                "hands": hand_details,
                "spatial": self.spatial_snapshot(),
                "interaction": interaction or "none",
            }
            if not self.camera_on:
                reason = self.last_error or self.camera.last_error or "camera stopped"
                try:
                    if (not self.camera.last_error and
                            self.bus.publish_count == 0):
                        reason = "camera stopped"
                except Exception:
                    pass
                common.update({"state": "UNAVAILABLE", "reason": reason[:120]})
                return common
            if not self.perception.available:
                common.update({
                    "state": "DEGRADED",
                    "reason": self.perception.last_error[:120]
                    or "hand model unavailable",
                })
                return common
            try:
                covered = bool(self.camera.lens_covered)
            except Exception:
                covered = False
            if covered:
                common.update({
                    "state": "DEGRADED",
                    "reason": "camera lens covered (suspected); uncover to resume",
                })
                return common
            if fresh is None and self.bus.publish_count == 0:
                common.update({
                    "state": "DEGRADED",
                    "reason": "starting up, no frames yet",
                })
                return common
            common.update({
                "state": "AVAILABLE",
                "reason": "streaming, %d hand(s) in view" % (
                    fresh.count() if fresh is not None else 0),
            })
            return common
        except Exception as e:
            return {"state": "UNAVAILABLE", "reason": str(e)[:120]}


def get_hand_camera(index: int = 0, fps: float = 10.0) -> HandCamera:
    """Return the one process-local DUDE capture source for an index."""
    key = (str(os.name).lower(), int(index))
    with _CAMERA_OWNER_LOCK:
        source = _CAMERA_SOURCES.get(key)
        if source is None:
            source = HandCamera(index=index, fps=fps)
            _CAMERA_SOURCES[key] = source
        return source


def get_hand_tracker(bus=None, camera_index: int = 0) -> HandTracker:
    return HandTracker(bus=bus, camera_index=camera_index)
