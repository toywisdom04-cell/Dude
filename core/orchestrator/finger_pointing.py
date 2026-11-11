"""Stable per-hand active-finger selection for pointing interactions."""
from __future__ import annotations

import math
from typing import Optional


FINGER_TIP_LANDMARKS = {
    "thumb": 4,
    "index": 8,
    "middle": 12,
    "ring": 16,
    "pinky": 20,
}
FINGER_PIP_LANDMARKS = {
    "index": 6,
    "middle": 10,
    "ring": 14,
    "pinky": 18,
}

_POINT_THRESHOLD = 1.15
_SWITCH_RATIO = 1.20


def _distance(a, b) -> float:
    return math.hypot(float(a[0]) - float(b[0]),
                      float(a[1]) - float(b[1]))


def pointing_finger(hand, previous: Optional[str] = None) -> str:
    """Return a stable finger name for a pointing pose.

    The previous finger wins ties and small noise changes. A different
    finger must be at least 20% stronger before control is handed over.
    """
    points = getattr(hand, "landmarks", ()) or ()
    if len(points) <= 20:
        return previous if previous in FINGER_TIP_LANDMARKS else "index"
    wrist = points[0]
    ratios = {}
    for name, pip_index in FINGER_PIP_LANDMARKS.items():
        tip_index = FINGER_TIP_LANDMARKS[name]
        ratios[name] = (
            _distance(points[tip_index], wrist)
            / max(_distance(points[pip_index], wrist), 0.001))
    strongest = max(ratios, key=ratios.get)
    if (previous in ratios and ratios[previous] >= _POINT_THRESHOLD
            and ratios[strongest] <= ratios[previous] * _SWITCH_RATIO):
        return previous
    if ratios[strongest] >= _POINT_THRESHOLD:
        return strongest
    return previous if previous in FINGER_TIP_LANDMARKS else "index"


def point_in_frame(point: Optional[tuple], margin: float = 0.0) -> bool:
    if point is None or len(point) < 2:
        return False
    return (float(margin) <= float(point[0]) <= 1.0 - float(margin)
            and float(margin) <= float(point[1]) <= 1.0 - float(margin))


def smooth_point(previous: Optional[tuple], raw: Optional[tuple],
                 alpha: float = 0.40, deadband: float = 0.004
                 ) -> Optional[tuple]:
    """Apply a small deadband plus EMA to a noisy fingertip point."""
    if raw is None:
        return previous
    if previous is None:
        return (float(raw[0]), float(raw[1]))
    distance = _distance(previous, raw)
    if distance <= deadband:
        return (float(previous[0]), float(previous[1]))
    alpha = max(0.05, min(1.0, float(alpha)))
    return (
        float(previous[0] + (float(raw[0]) - float(previous[0])) * alpha),
        float(previous[1] + (float(raw[1]) - float(previous[1])) * alpha),
    )


def finger_point(hand, finger: str = "index") -> Optional[tuple]:
    points = getattr(hand, "landmarks", ()) or ()
    index = FINGER_TIP_LANDMARKS.get(finger, FINGER_TIP_LANDMARKS["index"])
    if len(points) > index and len(points[index]) >= 2:
        return (float(points[index][0]), float(points[index][1]))
    return None


__all__ = [
    "FINGER_TIP_LANDMARKS", "FINGER_PIP_LANDMARKS",
    "finger_point", "point_in_frame", "pointing_finger", "smooth_point",
]
