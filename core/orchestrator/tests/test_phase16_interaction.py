"""Phase 16 interaction and shared-camera regression tests.

Synthetic landmarks prove the semantic boundary without hardware. Camera
lifecycle tests use an injected OpenCV stub; a separate live proof exercises
the actual Windows backend when a camera is available.
"""
import asyncio
import inspect
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from core.capability_bus import CapabilityBus
from core.orchestrator.hand_tracking import (
    Gesture,
    HandActionBridge,
    HandCamera,
    HandInteraction,
    HandInteractionMapper,
    HandTracker,
    HandObservation,
    HandPerception,
    HandState,
    SemanticInteraction,
    classify_gesture,
)
from core.orchestrator.state import (
    Action,
    ExpectedResult,
    OrchestratorState,
    SubGoal,
    TargetSpec,
    TaskState,
    VerificationMethod,
    VerificationResult,
)
from core.orchestrator.task_engine import TaskEngine
from core.orchestrator.finger_pointing import pointing_finger, smooth_point


def _landmarks(point=(0.5, 0.5), gesture=None):
    """Simple valid geometry; mapper tests replace palm/gesture directly."""
    p = list(point)
    return [tuple(p) for _ in range(21)]


def _pointing_landmarks():
    lm = list(_landmarks((0.5, 0.8)))
    lm[5], lm[6], lm[7], lm[8] = (
        (0.40, 0.60), (0.40, 0.45), (0.40, 0.32), (0.40, 0.20))
    return lm


def _claw_landmarks():
    lm = list(_landmarks((0.5, 0.5)))
    # Fold index/middle/ring while leaving the pinky extended; this is the
    # BareHands claw envelope rather than a fist or pinch.
    for mcp, x in ((5, 0.40), (9, 0.48), (13, 0.56)):
        lm[mcp] = (x, 0.60)
        lm[mcp + 1] = (x, 0.45)
        lm[mcp + 2] = (x, 0.50)
        lm[mcp + 3] = (x, 0.53)
    lm[17], lm[18], lm[19], lm[20] = (
        (0.63, 0.60), (0.64, 0.45), (0.64, 0.30), (0.64, 0.18))
    lm[1], lm[2], lm[3], lm[4] = (
        (0.58, 0.62), (0.59, 0.61), (0.60, 0.60), (0.61, 0.59))
    return lm


def _prayer_landmarks(point):
    """BareHands-style vertical open-palm geometry for clap tests."""
    x, _ = point
    lm = [(x, 0.70)]
    lm += [(x - 0.04, 0.68), (x - 0.08, 0.64), (x - 0.10, 0.60),
           (x - 0.12, 0.56)]
    for dx in (0.0, 0.02, 0.04, 0.06):
        lm += [(x + dx, 0.48), (x + dx, 0.30),
               (x + dx, 0.18), (x + dx, 0.08)]
    return lm


def _hand(label, point, gesture, landmarks=None):
    return HandObservation(
        label=label,
        identity=label,
        palm=tuple(point),
        wrist=tuple(point),
        landmarks=tuple(landmarks or _landmarks(point)),
        gesture=gesture,
        gesture_confidence=0.9,
        handedness_score=0.9,
    )


def _state(*hands, frame_id=1):
    return HandState(hands=tuple(hands), frame_id=frame_id)


class TestPhase16TwoHandState(unittest.TestCase):
    def test_two_hands_and_stable_identity_survive_processing(self):
        state = _state(
            _hand("left", (0.25, 0.4), Gesture.POINTING),
            _hand("right", (0.75, 0.4), Gesture.PINCH),
            frame_id=7,
        )
        self.assertEqual(state.count(), 2)
        self.assertEqual(state.identities(), ("left", "right"))
        self.assertIs(state.left, state.hands[0])
        self.assertIs(state.right, state.hands[1])
        self.assertTrue(state.two_hands())
        self.assertEqual(state.left.gesture, Gesture.POINTING)
        self.assertEqual(state.right.gesture, Gesture.PINCH)
        self.assertEqual(len(state.left.landmarks), 21)
        self.assertEqual(len(state.right.landmarks), 21)

    def test_one_hand_disappearing_does_not_change_surviving_hand(self):
        mapper = HandInteractionMapper()
        both = _state(
            _hand("left", (0.25, 0.4), Gesture.POINTING),
            _hand("right", (0.75, 0.4), Gesture.POINTING),
            frame_id=1,
        )
        stable = {
            "left": (Gesture.POINTING, 3),
            "right": (Gesture.POINTING, 3),
        }
        mapper.observe(both, stable_by_hand=stable, now=1.0)
        only_left = _state(
            _hand("left", (0.26, 0.4), Gesture.POINTING), frame_id=2)
        result = mapper.observe(
            only_left, stable_by_hand={"left": (Gesture.POINTING, 3)},
            now=1.1)
        self.assertEqual(result.hand_ids, ("left",))
        self.assertEqual(result.name, SemanticInteraction.POINTER)

    def test_both_hands_can_carry_simultaneous_gestures(self):
        mapper = HandInteractionMapper()
        state = _state(
            _hand("left", (0.25, 0.4), Gesture.PINCH),
            _hand("right", (0.75, 0.4), Gesture.OPEN),
            frame_id=3,
        )
        result = mapper.observe(
            state,
            stable_by_hand={
                "left": (Gesture.PINCH, 4),
                "right": (Gesture.OPEN, 4),
            },
            now=2.0,
        )
        self.assertEqual(result.hand_ids, ("left", "right"))
        self.assertEqual(result.gestures, ("pinch", "open"))
        self.assertEqual(result.votes, 4)

    def test_mixed_two_hand_pinch_can_emit_one_independent_click(self):
        mapper = HandInteractionMapper()
        stable = {
            "left": (Gesture.PINCH, 4),
            "right": (Gesture.OPEN, 4),
        }
        for index, timestamp in enumerate((1.0, 1.1, 1.2, 1.3), start=1):
            result = mapper.observe(
                _state(
                    _hand("left", (0.25, 0.4), Gesture.PINCH),
                    _hand("right", (0.75, 0.4), Gesture.OPEN),
                    frame_id=index),
                stable_by_hand=stable, now=timestamp)
        self.assertEqual(result.name, SemanticInteraction.TAP)
        self.assertEqual(result.hand_ids, ("left",))
        self.assertEqual(result.action_type, "click_at")

    def test_hand_tracker_preserves_detector_slot_identity_when_order_changes(self):
        tracker = __import__(
            "core.orchestrator.hand_tracking", fromlist=["HandTracker"]
        ).HandTracker()
        first = HandState(hands=(
            HandObservation(label="left", identity="hand_0",
                            palm=(0.2, 0.4), wrist=(0.2, 0.4),
                            gesture=Gesture.POINTING,
                            handedness_score=0.9),
            HandObservation(label="right", identity="hand_1",
                            palm=(0.8, 0.4), wrist=(0.8, 0.4),
                            gesture=Gesture.PINCH,
                            handedness_score=0.9),
        ), frame_id=1)
        second = HandState(hands=(
            HandObservation(label="right", identity="hand_1",
                            palm=(0.8, 0.4), wrist=(0.8, 0.4),
                            gesture=Gesture.PINCH,
                            handedness_score=0.9),
            HandObservation(label="left", identity="hand_0",
                            palm=(0.2, 0.4), wrist=(0.2, 0.4),
                            gesture=Gesture.POINTING,
                            handedness_score=0.9),
        ), frame_id=2)
        frames = iter((1, 2, 3))
        states = iter((first, second, second))
        tracker.camera = SimpleNamespace(
            interval=0.01,
            latest=lambda: (object(), next(frames)),
        )
        tracker.perception = SimpleNamespace(
            process_frame=lambda _frame: next(states),
        )
        for _ in range(3):
            tracker.tick()
        stable = tracker.stable_by_hand()
        self.assertEqual(stable["hand_0"][0], Gesture.POINTING)
        self.assertEqual(stable["hand_1"][0], Gesture.PINCH)
        self.assertEqual(tracker.stable(), (Gesture.UNKNOWN, 0))

    def test_hand_tracker_stabilizes_each_identity_without_primary_collapse(self):
        tracker = __import__(
            "core.orchestrator.hand_tracking", fromlist=["HandTracker"]
        ).HandTracker()
        state = _state(
            _hand("left", (0.25, 0.4), Gesture.POINTING),
            _hand("right", (0.75, 0.4), Gesture.PINCH),
            frame_id=1,
        )
        frame_ids = iter((1, 2, 3))
        tracker.camera = SimpleNamespace(
            interval=0.01,
            latest=lambda: (object(), next(frame_ids)),
        )
        tracker.perception = SimpleNamespace(
            process_frame=lambda _frame: state,
        )
        tracker.tick()
        tracker.tick()
        tracker.tick()
        stable = tracker.stable_by_hand()
        self.assertEqual(stable["left"][0], Gesture.POINTING)
        self.assertEqual(stable["right"][0], Gesture.PINCH)
        self.assertEqual(tracker.stable(), (Gesture.UNKNOWN, 0))
        self.assertEqual(tracker.bus.get().count(), 2)


    def test_detector_order_changes_keep_stable_identity_and_preserve_one_hand(self):
        perception = HandPerception()
        first = perception.state_from_landmarks([
            (_landmarks((0.20, 0.40)), "Left", 0.9),
            (_landmarks((0.80, 0.40)), "Right", 0.9),
        ])
        first_ids = {(round(h.palm[0], 2), round(h.palm[1], 2)):
                     h.identity for h in first.hands}
        second = perception.state_from_landmarks([
            (_landmarks((0.80, 0.42)), "Right", 0.9),
            (_landmarks((0.22, 0.41)), "Left", 0.9),
        ])
        second_ids = {(round(h.palm[0], 2), round(h.palm[1], 2)):
                      h.identity for h in second.hands}
        self.assertEqual(first_ids[(0.20, 0.40)],
                         second_ids[(0.22, 0.41)])
        self.assertEqual(first_ids[(0.80, 0.40)],
                         second_ids[(0.80, 0.42)])
        self.assertEqual(len(set(h.identity for h in second.hands)), 2)

        one = perception.state_from_landmarks([
            (_landmarks((0.22, 0.41)), "Left", 0.9),
        ])
        self.assertEqual(one.count(), 1)
        self.assertEqual(one.hands[0].identity,
                         first_ids[(0.20, 0.40)])
        self.assertNotIn(first_ids[(0.80, 0.40)],
                         {h.identity for h in one.hands})
        again = perception.state_from_landmarks([
            (_landmarks((0.80, 0.40)), "Right", 0.9),
            (_landmarks((0.20, 0.40)), "Left", 0.9),
        ])
        self.assertTrue({h.identity for h in again.hands}.issubset(
            {"hand_0", "hand_1"}))


class TestPhase16SemanticInteraction(unittest.TestCase):
    def test_claw_is_distinct_and_rearms_only_after_release(self):
        gesture, confidence = classify_gesture(_claw_landmarks())
        self.assertEqual(gesture, Gesture.CLAW)
        self.assertGreaterEqual(confidence, 0.6)
        mapper = HandInteractionMapper()
        stable_open = {"right": (Gesture.OPEN, 4)}
        stable_claw = {"right": (Gesture.CLAW, 4)}
        mapper.observe(
            _state(_hand("right", (0.5, 0.5), Gesture.OPEN), frame_id=1),
            stable_by_hand=stable_open, now=59.8)
        emitted = None
        for index in range(14):
            result = mapper.observe(
                _state(_hand("right", (0.5, 0.5), Gesture.CLAW),
                       frame_id=2 + index),
                stable_by_hand=stable_claw, now=59.9 + index * 0.03)
            if emitted is None and result.name == SemanticInteraction.CLAW:
                emitted = result
        self.assertIsNotNone(emitted)
        self.assertEqual(emitted.action_type, "")
        held = mapper.observe(
            _state(_hand("right", (0.5, 0.5), Gesture.CLAW), frame_id=30),
            stable_by_hand=stable_claw, now=60.4)
        self.assertEqual(held.action_type, "")
        released = mapper.observe(
            _state(_hand("right", (0.5, 0.5), Gesture.OPEN), frame_id=31),
            stable_by_hand=stable_open, now=60.5)
        self.assertEqual(released.action_type, "")
        mapper.observe(
            _state(_hand("right", (0.5, 0.5), Gesture.OPEN), frame_id=32),
            stable_by_hand=stable_open, now=60.6)
        rearmed = None
        for index in range(14):
            result = mapper.observe(
                _state(_hand("right", (0.5, 0.5), Gesture.CLAW),
                       frame_id=33 + index),
                stable_by_hand=stable_claw, now=60.7 + index * 0.03)
            if rearmed is None and result.name == SemanticInteraction.CLAW:
                rearmed = result
        self.assertIsNotNone(rearmed)
        self.assertEqual(rearmed.action_type, "")

    def test_grounding_uses_the_visible_spatial_pointer_coordinate(self):
        tracker = HandTracker.__new__(HandTracker)
        tracker._lock = threading.RLock()
        tracker._visual_bus = object()
        tracker.mapper = SimpleNamespace(map=lambda _x, _y: (10, 10))
        tracker.spatial_layer = SimpleNamespace(snapshot=lambda: {
            "pointers": [{"hand_id": "right", "screen_xy": (640, 480)}],
        })
        interaction = HandInteraction(
            name=SemanticInteraction.TAP, hand_ids=("right",),
            gestures=("pinch",), point=(0.1, 0.1), action_type="click_at")
        with mock.patch(
                "core.orchestrator.hand_tracking.relate_to_visual",
                return_value={"element": "Target"}) as relate:
            result = tracker._ground_interaction(interaction)
        self.assertEqual(result.screen_point, (640, 480))
        relate.assert_called_once_with((640, 480), tracker._visual_bus)

    def test_non_spatial_hand_keeps_click_when_other_hand_is_spatial(self):
        class _Mapper:
            @staticmethod
            def _identity(hand, _index):
                return "hand_0" if hand.label == "left" else "hand_1"

            @staticmethod
            def _point_for(_hand, _gesture, _identity):
                return (0.5, 0.5)

            @staticmethod
            def _observe_one_hand(_state, _hands, _stable, _points, _timestamp):
                return HandInteraction(
                    name=SemanticInteraction.TAP,
                    hand_ids=("hand_1",), gestures=("pinch",),
                    point=(0.5, 0.5), action_type="click_at")

        tracker = HandTracker.__new__(HandTracker)
        tracker._lock = threading.RLock()
        tracker._visual_bus = None
        tracker.interaction_mapper = _Mapper()
        state = _state(
            _hand("left", (0.2, 0.4), Gesture.PINCH),
            _hand("right", (0.8, 0.4), Gesture.PINCH),
            frame_id=1)
        result = tracker._prefer_non_spatial_interaction(
            state,
            HandInteraction(
                name=SemanticInteraction.TWO_HAND_SCALE,
                hand_ids=("hand_0", "hand_1"), gestures=("pinch", "pinch"),
                point=(0.5, 0.4), action_type="resize_window"),
            {"hand_0": (Gesture.PINCH, 4),
             "hand_1": (Gesture.PINCH, 4)},
            {"hands": [{"hand_id": "hand_0", "grabbed": True}]}, 1.0)
        self.assertEqual(result.action_type, "click_at")
        self.assertEqual(result.hand_ids, ("hand_1",))

    def test_three_finger_downward_drag_proposes_window_minimize(self):
        mapper = HandInteractionMapper()
        stable = {"right": (Gesture.THREE_FINGER, 4)}
        for index, point in enumerate(
                ((0.50, 0.35), (0.50, 0.42), (0.50, 0.50),
                 (0.50, 0.90)), start=1):
            result = mapper.observe(
                _state(_hand("right", point, Gesture.THREE_FINGER),
                       frame_id=index),
                stable_by_hand=stable, now=10.0 + index * 0.1)
        self.assertEqual(result.name, SemanticInteraction.WINDOW_MINIMIZE)
        self.assertEqual(result.action_type, "window_operation")
        self.assertEqual(result.action_parameters["action"], "minimize")

    def test_fast_open_hand_flick_never_executes_scroll(self):
        mapper = HandInteractionMapper()
        first = mapper.observe(
            _state(_hand("right", (0.40, 0.5), Gesture.OPEN), frame_id=1),
            stable_by_hand={"right": (Gesture.OPEN, 4)}, now=1.0)
        self.assertEqual(first.action_type, "")
        result = mapper.observe(
            _state(_hand("right", (0.80, 0.5), Gesture.OPEN), frame_id=2),
            stable_by_hand={"right": (Gesture.OPEN, 4)}, now=1.05)
        self.assertEqual(result.name, SemanticInteraction.FLICK)
        self.assertEqual(result.action_type, "")

    def test_point_to_pinch_does_not_emit_a_point_release_click(self):
        mapper = HandInteractionMapper()
        mapper.observe(
            _state(_hand("right", (0.4, 0.2), Gesture.POINTING)),
            stable_by_hand={"right": (Gesture.POINTING, 4)}, now=1.0)
        result = mapper.observe(
            _state(_hand("right", (0.4, 0.2), Gesture.PINCH), frame_id=2),
            stable_by_hand={"right": (Gesture.PINCH, 4)}, now=1.1)
        self.assertEqual(result.name, SemanticInteraction.NONE)
        self.assertEqual(result.action_type, "")

    def test_pinch_release_tap_is_stabilized_before_semantic_action(self):
        mapper = HandInteractionMapper()
        state = _state(_hand("right", (0.5, 0.5), Gesture.PINCH), frame_id=1)
        stable = {"right": (Gesture.PINCH, 3)}
        first = mapper.observe(state, stable_by_hand=stable, now=10.0)
        self.assertEqual(first.action_type, "")
        release = _state(
            _hand("right", (0.51, 0.5), Gesture.OPEN), frame_id=4)
        result = mapper.observe(
            release, stable_by_hand={"right": (Gesture.OPEN, 3)}, now=10.2)
        self.assertEqual(result.name, SemanticInteraction.TAP)
        self.assertEqual(result.action_type, "click_at")
        self.assertEqual(result.hand_ids, ("right",))
        self.assertTrue(result.stable)
        self.assertIn("BareHands stage.html", result.provenance)

    def test_any_single_finger_points_with_that_fingertip(self):
        lm = list(_pointing_landmarks())
        # Fold the index and extend only the middle finger.
        lm[5], lm[6], lm[7], lm[8] = (
            (0.40, 0.60), (0.40, 0.58), (0.40, 0.55), (0.40, 0.68))
        lm[9], lm[10], lm[11], lm[12] = (
            (0.48, 0.60), (0.48, 0.40), (0.48, 0.25), (0.48, 0.15))
        self.assertEqual(classify_gesture(lm)[0], Gesture.POINTING)
        hand = HandObservation(
            label="right", identity="right", palm=(0.45, 0.45),
            wrist=(0.5, 0.8), landmarks=tuple(lm),
            gesture=Gesture.POINTING, gesture_confidence=0.9,
            handedness_score=0.9)
        result = HandInteractionMapper().observe(
            _state(hand, frame_id=1),
            stable_by_hand={"right": (Gesture.POINTING, 3)}, now=1.0)
        self.assertEqual(result.name, SemanticInteraction.POINTER)
        self.assertEqual(result.point, (0.48, 0.15))

    def test_active_finger_hysteresis_resists_small_geometry_noise(self):
        lm = list(_pointing_landmarks())
        lm[5], lm[6], lm[7], lm[8] = (
            (0.40, 0.60), (0.40, 0.58), (0.40, 0.55), (0.40, 0.68))
        lm[9], lm[10], lm[11], lm[12] = (
            (0.48, 0.60), (0.48, 0.40), (0.48, 0.25), (0.48, 0.15))
        hand = HandObservation(
            label="right", identity="right", wrist=(0.5, 0.8),
            landmarks=tuple(lm), gesture=Gesture.POINTING)
        self.assertEqual(pointing_finger(hand), "middle")
        noisy = list(lm)
        noisy[8] = (0.40, 0.55)
        noisy_hand = HandObservation(
            label="right", identity="right", wrist=(0.5, 0.8),
            landmarks=tuple(noisy), gesture=Gesture.POINTING)
        self.assertEqual(pointing_finger(noisy_hand, "middle"), "middle")
        strong_index = list(lm)
        strong_index[8] = (0.40, 0.30)
        strong_hand = HandObservation(
            label="right", identity="right", wrist=(0.5, 0.8),
            landmarks=tuple(strong_index), gesture=Gesture.POINTING)
        self.assertEqual(pointing_finger(strong_hand, "middle"), "index")

    def test_fingertip_smoothing_deadband_ignores_small_jitter(self):
        stable = smooth_point((0.50, 0.50), (0.502, 0.501))
        self.assertEqual(stable, (0.50, 0.50))
        moved = smooth_point((0.50, 0.50), (0.60, 0.50))
        self.assertGreater(moved[0], stable[0])
        self.assertLess(moved[0], 0.60)

    def test_one_finger_dwell_emits_one_semantic_tap(self):
        mapper = HandInteractionMapper()
        hand = _hand("right", (0.4, 0.2), Gesture.POINTING,
                     _pointing_landmarks())
        first = mapper.observe(
            _state(hand, frame_id=1),
            stable_by_hand={"right": (Gesture.POINTING, 3)}, now=1.0)
        self.assertEqual(first.name, SemanticInteraction.POINTER)
        dwell = None
        for index, timestamp in enumerate((1.1, 1.2, 1.3, 1.4), start=2):
            result = mapper.observe(
                _state(hand, frame_id=index),
                stable_by_hand={"right": (Gesture.POINTING, 3)},
                now=timestamp)
            if result.name == SemanticInteraction.TAP:
                dwell = result
        self.assertIsNotNone(dwell)
        self.assertEqual(dwell.action_type, "click_at")
        self.assertEqual(dwell.point, (0.4, 0.2))
        repeat = mapper.observe(
            _state(hand, frame_id=9),
            stable_by_hand={"right": (Gesture.POINTING, 3)}, now=1.8)
        self.assertEqual(repeat.name, SemanticInteraction.POINTER)

    def test_point_release_allows_bounded_stabilizer_latency(self):
        mapper = HandInteractionMapper()
        mapper.observe(
            _state(_hand("right", (0.4, 0.2), Gesture.POINTING)),
            stable_by_hand={"right": (Gesture.POINTING, 3)}, now=2.0)
        result = mapper.observe(
            _state(_hand("right", (0.4, 0.2), Gesture.OPEN), frame_id=2),
            stable_by_hand={"right": (Gesture.OPEN, 3)}, now=2.5)
        self.assertEqual(result.name, SemanticInteraction.TAP)
        self.assertEqual(result.action_type, "click_at")

    def test_one_finger_point_release_emits_one_semantic_tap(self):
        mapper = HandInteractionMapper()
        pointing = _hand("right", (0.4, 0.2), Gesture.POINTING,
                         _pointing_landmarks())
        mapper.observe(
            _state(pointing, frame_id=1),
            stable_by_hand={"right": (Gesture.POINTING, 3)}, now=1.0)
        released = _hand("right", (0.4, 0.2), Gesture.OPEN,
                         _pointing_landmarks())
        result = mapper.observe(
            _state(released, frame_id=2),
            stable_by_hand={"right": (Gesture.OPEN, 3)}, now=1.2)
        self.assertEqual(result.name, SemanticInteraction.TAP)
        self.assertEqual(result.action_type, "click_at")
        self.assertEqual(result.point, (0.4, 0.2))

    def test_semantic_point_matches_visible_index_fingertip(self):
        mapper = HandInteractionMapper()
        tip_landmarks = _landmarks((0.8, 0.4))
        tip_landmarks[8] = (0.8, 0.4)
        hand = HandObservation(
            label="right", identity="right", palm=(0.2, 0.4),
            wrist=(0.2, 0.4), landmarks=tuple(tip_landmarks),
            gesture=Gesture.POINTING, gesture_confidence=0.9,
            handedness_score=0.9)
        result = mapper.observe(
            _state(hand, frame_id=1),
            stable_by_hand={"right": (Gesture.POINTING, 3)}, now=1.0)
        self.assertEqual(result.point, (0.8, 0.4))

    def test_pinch_movement_drags_for_the_desktop_target(self):
        """Pinch-and-move must DRAG, not click.

        This branch used to return ``action_type="click_at"``, so moving an
        application with a pinch just clicked wherever the finger finished.
        A completed pinch that travelled far enough is a drag.
        """
        mapper = HandInteractionMapper()
        mapper.observe(
            _state(_hand("left", (0.2, 0.5), Gesture.PINCH)),
            stable_by_hand={"left": (Gesture.PINCH, 3)}, now=20.0)
        result = mapper.observe(
            _state(_hand("left", (0.45, 0.5), Gesture.OPEN), frame_id=2),
            stable_by_hand={"left": (Gesture.OPEN, 3)}, now=20.2)
        self.assertEqual(result.name, SemanticInteraction.DRAG)
        self.assertEqual(result.action_type, "drag")
        self.assertEqual(result.start_point, (0.2, 0.5))
        self.assertEqual(result.end_point, (0.45, 0.5))
        self.assertEqual(result.action_parameters["x0"], 0.2)
        self.assertEqual(result.action_parameters["x1"], 0.45)

    def test_hold_then_release_is_a_single_click_proposal(self):
        mapper = HandInteractionMapper()
        mapper.observe(
            _state(_hand("right", (0.5, 0.5), Gesture.PINCH)),
            stable_by_hand={"right": (Gesture.PINCH, 3)}, now=30.0)
        for index, timestamp in enumerate((30.05, 30.1, 30.15), start=2):
            mapper.observe(
                _state(_hand("right", (0.5, 0.5), Gesture.PINCH),
                       frame_id=index),
                stable_by_hand={"right": (Gesture.PINCH, 3)},
                now=timestamp)
        result = mapper.observe(
            _state(_hand("right", (0.5, 0.5), Gesture.PINCH), frame_id=5),
            stable_by_hand={"right": (Gesture.PINCH, 3)}, now=30.4)
        self.assertEqual(result.name, SemanticInteraction.TAP)
        self.assertEqual(result.action_type, "click_at")
        released = mapper.observe(
            _state(_hand("right", (0.5, 0.5), Gesture.OPEN), frame_id=6),
            stable_by_hand={"right": (Gesture.OPEN, 3)}, now=30.5)
        self.assertEqual(released.name, SemanticInteraction.NONE)
        self.assertEqual(released.action_type, "")

    def test_two_hand_scale_requires_both_hands(self):
        mapper = HandInteractionMapper()
        first = _state(
            _hand("left", (0.25, 0.5), Gesture.PINCH),
            _hand("right", (0.75, 0.5), Gesture.PINCH), frame_id=1)
        mapper.observe(
            first,
            stable_by_hand={
                "left": (Gesture.PINCH, 4),
                "right": (Gesture.PINCH, 4),
            }, now=40.0)
        second = _state(
            _hand("left", (0.35, 0.5), Gesture.PINCH),
            _hand("right", (0.65, 0.5), Gesture.PINCH), frame_id=2)
        result = mapper.observe(
            second,
            stable_by_hand={
                "left": (Gesture.PINCH, 4),
                "right": (Gesture.PINCH, 4),
            }, now=40.1)
        self.assertEqual(result.name, SemanticInteraction.TWO_HAND_SCALE)
        self.assertEqual(result.hand_ids, ("left", "right"))
        self.assertEqual(result.action_type, "resize_window")
        self.assertIn("scale_factor", result.action_parameters)
        duplicate = mapper.observe(
            _state(
                _hand("left", (0.36, 0.5), Gesture.PINCH),
                _hand("right", (0.64, 0.5), Gesture.PINCH), frame_id=3),
            stable_by_hand={
                "left": (Gesture.PINCH, 4),
                "right": (Gesture.PINCH, 4),
            }, now=40.2)
        self.assertEqual(duplicate.action_type, "")

        one = _state(_hand("left", (0.35, 0.5), Gesture.PINCH), frame_id=4)
        result = mapper.observe(
            one, stable_by_hand={"left": (Gesture.PINCH, 4)}, now=40.3)
        self.assertNotEqual(result.name, SemanticInteraction.TWO_HAND_SCALE)

    def test_two_hand_clap_is_one_semantic_confirmation(self):
        mapper = HandInteractionMapper()
        stable = {
            "left": (Gesture.OPEN, 4),
            "right": (Gesture.OPEN, 4),
        }
        apart = _state(
            _hand("left", (0.1, 0.5), Gesture.OPEN,
                  _prayer_landmarks((0.1, 0.5))),
            _hand("right", (0.9, 0.5), Gesture.OPEN,
                  _prayer_landmarks((0.9, 0.5))), frame_id=1)
        mapper.observe(apart, stable_by_hand=stable, now=50.0)
        middle = _state(
            _hand("left", (0.3, 0.5), Gesture.OPEN,
                  _prayer_landmarks((0.3, 0.5))),
            _hand("right", (0.7, 0.5), Gesture.OPEN,
                  _prayer_landmarks((0.7, 0.5))), frame_id=2)
        mapper.observe(middle, stable_by_hand=stable, now=50.05)
        together = _state(
            _hand("left", (0.48, 0.5), Gesture.OPEN,
                  _prayer_landmarks((0.48, 0.5))),
            _hand("right", (0.52, 0.5), Gesture.OPEN,
                  _prayer_landmarks((0.52, 0.5))), frame_id=3)
        result = mapper.observe(together, stable_by_hand=stable, now=50.1)
        self.assertEqual(result.name, SemanticInteraction.CLAP)
        self.assertEqual(result.action_type, "click_at")
        self.assertEqual(result.hand_ids, ("left", "right"))
        self.assertEqual(result.target_description,
                         "two-hand confirmation target")
        duplicate = mapper.observe(
            together, stable_by_hand=stable, now=50.11)
        self.assertEqual(duplicate.action_type, "")


class TestPhase16ActionBoundary(unittest.TestCase):
    def test_semantic_gesture_becomes_existing_action_representation(self):
        engine = TaskEngine.__new__(TaskEngine)
        engine._task_state = TaskState(goal="confirm target")
        engine._task_state.subgoals = [SubGoal(
            description="confirm the selected target", action_type="click")]
        engine._task_state.current_step = 0
        engine._pending_hand_action = None
        interaction = type("Interaction", (), {
            "name": SemanticInteraction.TAP,
            "action_type": "click_at",
            "point": (640, 360),
            "action_parameters": {},
            "target_description": "hand pinch target",
            "target": {"element": "Save", "kind": "uia"},
            "votes": 5,
        })()
        bridge = HandActionBridge(engine)
        self.assertTrue(bridge(interaction))
        action = engine._pending_hand_action
        self.assertIsInstance(action, Action)
        self.assertEqual(action.task_id, engine._task_state.task_id)
        self.assertEqual(action.step_id, 0)
        self.assertEqual(action.action_type, "click_at")
        self.assertEqual(action.target.coordinates, (640, 360))
        interaction.screen_point = (321, 222)
        self.assertTrue(HandActionBridge(engine)(interaction))
        self.assertEqual(engine._pending_hand_action.target.coordinates,
                         (321, 222))
        interaction.screen_point = None
        self.assertEqual(action.side_effect_class, "NON_IDEMPOTENT")
        self.assertEqual(action.replay_policy, "DO_NOT_RETRY")
        self.assertEqual(action.verification_method.value, "screen_delta")

        engine._pending_hand_action = action
        interaction.target = {}
        self.assertFalse(bridge(interaction))
        self.assertIs(engine._pending_hand_action, action)

    def test_pinch_press_clicks_at_current_position_after_pointer_moves(self):
        mapper = HandInteractionMapper()
        first = _hand("right", (0.2, 0.5), Gesture.PINCH)
        moved = _hand("right", (0.8, 0.5), Gesture.PINCH)
        mapper.observe(
            _state(first, frame_id=1),
            stable_by_hand={"right": (Gesture.PINCH, 3)}, now=1.0)
        pressed = mapper.observe(
            _state(moved, frame_id=2),
            stable_by_hand={"right": (Gesture.PINCH, 3)}, now=1.35)
        self.assertEqual(pressed.name, SemanticInteraction.TAP)
        self.assertEqual(pressed.action_type, "click_at")
        self.assertAlmostEqual(pressed.point[0], 0.44, places=5)
        self.assertAlmostEqual(pressed.point[1], 0.5, places=5)

    def test_stable_near_pinch_cannot_emit_a_click(self):
        lm = list(_pointing_landmarks())
        lm[4] = (0.49, 0.20)
        hand = _hand("right", (0.5, 0.5), Gesture.PINCH, lm)
        mapper = HandInteractionMapper()
        stable = {"right": (Gesture.PINCH, 5)}
        for index, timestamp in enumerate((1.0, 1.1, 1.2, 1.3), start=1):
            result = mapper.observe(
                _state(hand, frame_id=index),
                stable_by_hand=stable, now=timestamp)
        self.assertEqual(result.name, SemanticInteraction.POINTER)
        self.assertEqual(result.action_type, "")

    def test_pinch_press_reaches_existing_action_bridge(self):
        mapper = HandInteractionMapper()
        pinch = _hand("right", (0.5, 0.5), Gesture.PINCH)
        mapper.observe(
            _state(pinch, frame_id=1),
            stable_by_hand={"right": (Gesture.PINCH, 3)}, now=50.0)
        for index, timestamp in enumerate((50.1, 50.2, 50.3), start=2):
            mapper.observe(
                _state(pinch, frame_id=index),
                stable_by_hand={"right": (Gesture.PINCH, 3)},
                now=timestamp)
        interaction = mapper.observe(
            _state(pinch, frame_id=5),
            stable_by_hand={"right": (Gesture.PINCH, 3)}, now=50.4)
        self.assertEqual(interaction.name, SemanticInteraction.TAP)
        self.assertEqual(interaction.action_type, "click_at")
        engine = TaskEngine.__new__(TaskEngine)
        engine._task_state = TaskState(goal="pinch target")
        engine._task_state.subgoals = [SubGoal(
            description="pinch target", action_type="click")]
        engine._pending_hand_action = None
        interaction = interaction.with_target({
            "element": "Proof Window", "kind": "window",
            "window": ("Proof", "Proof Window"),
            "window_rect": (0, 0, 800, 600),
        }, screen_point=(400, 300))
        self.assertTrue(HandActionBridge(engine)(interaction))
        self.assertEqual(engine._pending_hand_action.action_type, "click_at")
        self.assertEqual(engine._pending_hand_action.target.coordinates,
                         (400, 300))

    def test_idle_hand_dispatch_creates_task_and_runs_existing_loop(self):
        resumed = threading.Event()
        perception = SimpleNamespace(
            observe=lambda **_kwargs: SimpleNamespace(active_app="Proof"))
        engine = TaskEngine(
            mock.Mock(), perception, mock.Mock(), mock.Mock(), mock.Mock())

        async def fake_resume():
            resumed.set()

        engine.resume = fake_resume
        action = Action(
            task_id="", step_id=0, intent="hand pinch",
            action_type="click_at", target=TargetSpec(coordinates=(400, 300)),
            target_description="Proof Window",
            verification_method=VerificationMethod.SCREEN_DELTA)
        self.assertTrue(engine.dispatch_hand_action(action))
        self.assertIsNotNone(engine.task_state)
        self.assertEqual(action.task_id, engine.task_state.task_id)
        self.assertEqual(engine.task_state.current_subgoal().action_type,
                         "click_at")
        self.assertTrue(resumed.wait(1.0))

    def test_idle_hand_bridge_enters_task_dispatch_instead_of_rejecting(self):
        resumed = threading.Event()
        perception = SimpleNamespace(
            observe=lambda **_kwargs: SimpleNamespace(active_app="Proof"))
        engine = TaskEngine(
            mock.Mock(), perception, mock.Mock(), mock.Mock(), mock.Mock())

        async def fake_resume():
            resumed.set()

        engine.resume = fake_resume
        interaction = type("Interaction", (), {
            "name": SemanticInteraction.TAP,
            "action_type": "click_at",
            "point": (0.5, 0.5),
            "screen_point": (400, 300),
            "action_parameters": {},
            "target_description": "Proof Window",
            "target": {
                "element": "Proof Window", "kind": "window",
                "window": ("Proof", "Proof Window"),
                "window_rect": (0, 0, 800, 600),
            },
            "votes": 5,
        })()
        self.assertTrue(HandActionBridge(engine)(interaction))
        self.assertIsNotNone(engine.task_state)
        self.assertEqual(engine.task_state.current_subgoal().action_type,
                         "click_at")
        self.assertTrue(resumed.wait(1.0))

    def test_two_hand_scale_uses_existing_window_resize_action(self):
        engine = TaskEngine.__new__(TaskEngine)
        engine._task_state = TaskState(goal="resize target")
        engine._task_state.subgoals = [SubGoal(description="resize target")]
        engine._pending_hand_action = None
        interaction = type("Interaction", (), {
            "name": SemanticInteraction.TWO_HAND_SCALE,
            "action_type": "resize_window",
            "point": (400, 300),
            "action_parameters": {"scale_factor": 1.25},
            "target_description": "two-hand scale target",
            "target": {
                "element": "Proof Window",
                "kind": "window",
                "window": ("Proof", "Proof Window"),
                "window_rect": (100, 100, 800, 600),
            },
            "votes": 5,
        })()
        self.assertTrue(HandActionBridge(engine)(interaction))
        action = engine._pending_hand_action
        self.assertEqual(action.action_type, "resize_window")
        self.assertEqual(action.parameters["w"], 1000)
        self.assertEqual(action.parameters["h"], 750)
        self.assertEqual(action.verification_method.value,
                         "window_geometry_changed")

    def test_three_finger_minimize_uses_existing_window_action(self):
        from core.orchestrator.action_executor import ActionExecutor
        self.assertIn("window_operation", ActionExecutor.NO_GROUND_ACTIONS)
        engine = TaskEngine.__new__(TaskEngine)
        engine._task_state = TaskState(goal="minimize target")
        engine._task_state.subgoals = [SubGoal(description="minimize target")]
        engine._pending_hand_action = None
        interaction = type("Interaction", (), {
            "name": SemanticInteraction.WINDOW_MINIMIZE,
            "action_type": "window_operation",
            "point": (400, 300),
            "action_parameters": {"action": "minimize"},
            "target_description": "three finger minimize target",
            "target": {
                "element": "Proof Window", "kind": "window",
                "window": ("Proof", "Proof Window"),
                "window_rect": (100, 100, 800, 600),
            },
            "votes": 5,
        })()
        self.assertTrue(HandActionBridge(engine)(interaction))
        action = engine._pending_hand_action
        self.assertEqual(action.action_type, "window_operation")
        self.assertEqual(action.parameters["action"], "minimize")
        self.assertEqual(action.parameters["title"], "Proof Window")
        self.assertEqual(action.target.window_title_regex, "Proof Window")

    def test_window_geometry_verification_uses_existing_verification_engine(self):
        from core.orchestrator.verification import VerificationEngine

        action = Action(
            task_id="task", step_id=0, intent="scale",
            action_type="resize_window", target=__import__(
                "core.orchestrator.state", fromlist=["TargetSpec"]
            ).TargetSpec(window_title_regex="Proof"),
            target_description="Proof",
            verification_method=VerificationMethod.WINDOW_GEOMETRY_CHANGED,
            parameters={"title": "Proof", "expected_w": 1000,
                        "expected_h": 750},
        )
        expected = ExpectedResult(window_title="Proof")
        fake_win32 = SimpleNamespace(
            IsWindowVisible=lambda _hwnd: True,
            GetWindowText=lambda _hwnd: "Proof",
            GetWindowRect=lambda _hwnd: (10, 20, 1010, 770),
            EnumWindows=lambda callback, _param: callback(1, None),
        )
        with mock.patch.dict(sys.modules, {"win32gui": fake_win32}):
            result = asyncio.run(VerificationEngine(mock.Mock()).verify(
                action, expected, mock.Mock(), mock.Mock()))
        self.assertTrue(result.success)
        self.assertEqual(result.method,
                         VerificationMethod.WINDOW_GEOMETRY_CHANGED)

        gate = SimpleNamespace(
            requires_permission=lambda _action_type: True,
            ask=mock.Mock(return_value=True),
        )
        engine = TaskEngine(
            mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(),
            permission_gate=gate)
        engine.start_task("gesture confirmation")
        engine._task_state.subgoals = [SubGoal(
            description="confirm the current target", action_type="click")]
        engine._state = __import__(
            "core.orchestrator.state", fromlist=["OrchestratorState"]
        ).OrchestratorState.PLANNING
        state_module = __import__("core.orchestrator.state", fromlist=[
            "Action", "TargetSpec"])
        action = state_module.Action(
            task_id=engine._task_state.task_id, step_id=0, intent="tap",
            action_type="click_at",
            target=state_module.TargetSpec(coordinates=(100, 100)),
            target_description="gesture target")
        self.assertTrue(engine.propose_hand_action(action))
        asyncio.run(engine._do_planning())
        self.assertTrue(gate.ask.called)
        self.assertIs(engine._task_state.last_action, action)
        self.assertEqual(engine._state.value, "acting")

        engine = TaskEngine.__new__(TaskEngine)
        engine._task_state = TaskState(goal="target")
        engine._task_state.subgoals = [SubGoal(description="one")]
        engine._task_state.current_step = 1
        engine._pending_hand_action = None
        action = Action(
            task_id=engine._task_state.task_id, step_id=0, intent="tap",
            action_type="click_at", target=__import__(
                "core.orchestrator.state", fromlist=["TargetSpec"]
            ).TargetSpec(coordinates=(1, 1)),
            target_description="wrong step")
        self.assertFalse(engine.propose_hand_action(action))

    def test_executor_builds_drag_and_scroll_args_from_action_metadata(self):
        from core.orchestrator.action_executor import ActionExecutor
        executor = ActionExecutor.__new__(ActionExecutor)
        for action_type, params in (
            ("drag", {"x0": .1, "y0": .2, "x1": .3, "y1": .4}),
            ("scroll", {"direction": "up", "amount": 2}),
            ("window_operation", {"action": "minimize", "title": "Proof"}),
            ("resize_window", {"title": "Proof", "x": 10, "y": 20,
                                "w": 300, "h": 200}),
        ):
            action = Action(
                task_id="task", step_id=0, intent="gesture",
                action_type=action_type,
                target=__import__(
                    "core.orchestrator.state", fromlist=["TargetSpec"]
                ).TargetSpec(),
                target_description="target", parameters=params)
            grounded = type("Grounded", (), {"coordinates": None})()
            args = executor._build_tool_args(action, grounded)
            for key, value in params.items():
                self.assertEqual(args[key], value)

    def test_executor_builds_browser_observation_args(self):
        from core.orchestrator.action_executor import ActionExecutor
        from core.orchestrator.state import TargetSpec

        executor = ActionExecutor.__new__(ActionExecutor)
        grounded = type("Grounded", (), {"coordinates": None})()
        open_url = Action(
            task_id="task", step_id=0, intent="open portal",
            action_type="open_url", target=TargetSpec(
                text_match="https://portal.example.test"),
            target_description="https://portal.example.test")
        self.assertEqual(
            executor._build_tool_args(open_url, grounded),
            {"url": "https://portal.example.test"})

        read_screen = Action(
            task_id="task", step_id=1, intent="read screen",
            action_type="read_screen_text", target=TargetSpec(),
            target_description="visible portal tasks")
        self.assertEqual(executor._build_tool_args(read_screen, grounded), {})

    def test_gesture_crosses_existing_task_fence_executor_and_verification(self):
        from core.agent_state import AgentState

        gate = SimpleNamespace(
            requires_permission=lambda _action_type: True,
            ask=mock.Mock(return_value=True),
        )
        executor = mock.Mock()
        executor.execute = mock.AsyncMock(return_value=SimpleNamespace(
            success=True, status="executed", message="executed", error="",
            grounded_coordinates=None, duration_seconds=0.0,
            actual_result=None,
        ))
        verifier = mock.Mock()
        verifier.verify = mock.AsyncMock(return_value=VerificationResult(
            success=True, method=VerificationMethod.WINDOW_GEOMETRY_CHANGED,
            evidence="window geometry changed", confidence=1.0))
        before = SimpleNamespace(is_fresh=lambda max_age=5.0: True)
        after = SimpleNamespace(is_fresh=lambda max_age=5.0: True)
        perception = SimpleNamespace(
            observe=mock.Mock(return_value=after),
            detect_change=mock.Mock(return_value=None),
        )
        engine = TaskEngine(
            mock.Mock(), perception, executor, verifier, mock.Mock(),
            permission_gate=gate, agent_state=AgentState())
        task = engine.start_task("gesture proof")
        task.subgoals = [SubGoal(
            description="resize the selected window",
            action_type="resize_window")]
        engine._state = OrchestratorState.PLANNING
        engine._task_state.last_observation = before

        class _Status:
            def capability_status(self, _payload=None):
                return {"success": True, "data": {"capabilities": [
                    {"name": name, "state": "READY", "ready": True,
                     "failed": False, "reason": "healthy"}
                    for name in ("perception", "computer_tools", "uia",
                                 "verification")
                ]}}
        engine.set_capability_bus(_Status())
        interaction = type("Interaction", (), {
            "name": SemanticInteraction.TWO_HAND_SCALE,
            "action_type": "resize_window",
            "point": (400, 300),
            "action_parameters": {"scale_factor": 1.25},
            "target_description": "two-hand scale target",
            "target": {
                "element": "Proof Window", "kind": "window",
                "window": ("Proof", "Proof Window"),
                "window_rect": (100, 100, 800, 600),
            },
            "votes": 5,
        })()
        self.assertTrue(HandActionBridge(engine)(interaction))
        action = engine._pending_hand_action
        self.assertIsInstance(action, Action)

        async def run_boundaries():
            await engine._do_planning()
            self.assertTrue(gate.ask.called)
            self.assertEqual(engine.state, OrchestratorState.ACTING)
            await engine._do_acting()
            executor.execute.assert_awaited_once()
            self.assertEqual(engine.state, OrchestratorState.VERIFYING)
            await engine._do_verifying()

        asyncio.run(run_boundaries())
        self.assertTrue(task.verification_result.success)
        self.assertTrue(verifier.verify.await_count == 1)
        self.assertEqual(engine.action_fence_state()["decision"], "ALLOW")
        self.assertEqual(engine.action_fence_state()["state"], "RELEASED")

    def test_no_gesture_specific_executor_is_present(self):
        mapper = HandInteractionMapper()
        self.assertFalse(hasattr(mapper, "execute"))
        self.assertFalse(hasattr(mapper, "action_executor"))
        self.assertFalse(hasattr(HandActionBridge, "execute"))


    def test_camera_sharing_policy_is_application_agnostic(self):
        source = inspect.getsource(HandCamera)
        for application_name in ("Meet", "Teams", "Zoom", "WindowsCamera.exe"):
            self.assertNotIn(application_name.lower(), source.lower())
        self.assertEqual(
            set(inspect.signature(HandCamera.__init__).parameters),
            {"self", "index", "fps", "backend_preference"},
        )

        from core.holo_hand import FINGERS
        self.assertTrue(FINGERS)
        self.assertTrue(all(len(edge) == 2 for edge in FINGERS))


class TestPhase16CameraLifecycle(unittest.TestCase):
    def test_windows_msmf_is_selected_and_internal_owner_is_unique(self):
        class Cap:
            def __init__(self, *args):
                self.args = args
                self.released = False
            def isOpened(self):
                return True
            def read(self):
                return False, None
            def release(self):
                self.released = True

        opened = []
        stub = mock.Mock()
        stub.CAP_MSMF = 1400
        stub.CAP_DSHOW = 700
        def video_capture(*args):
            cap = Cap(*args)
            opened.append(cap)
            return cap
        stub.VideoCapture = video_capture
        first = HandCamera(index=41, fps=30)
        second = HandCamera(index=41, fps=30)
        try:
            with mock.patch.dict(sys.modules, {"cv2": stub}):
                self.assertTrue(first.start())
                self.assertEqual(first.capture_backend, "MSMF")
                self.assertTrue(first.shared_mode)
                self.assertEqual(first.camera_state, "OPENING")
                self.assertFalse(second.start())
                self.assertEqual(second.camera_state,
                                 "TEMPORARILY_UNAVAILABLE")
                self.assertEqual(len(opened), 1)
        finally:
            first.stop()
            second.stop()
        self.assertTrue(opened[0].released)
        self.assertEqual(first.camera_state, "STOPPED")
        self.assertFalse(first.shared_mode is None)

        # The owner lease is released, so a later explicit start can work.
        with mock.patch.dict(sys.modules, {"cv2": stub}):
            self.assertTrue(second.start())
        second.stop()

    def test_shared_source_reuses_one_handle_and_last_lease_releases(self):
        from core.orchestrator.hand_tracking import get_hand_camera

        class Cap:
            def __init__(self, *args):
                self.released = False
            def isOpened(self):
                return True
            def read(self):
                return False, None
            def release(self):
                self.released = True

        stub = mock.Mock()
        stub.CAP_MSMF = 1400
        stub.CAP_DSHOW = 700
        stub.VideoCapture = lambda *args: Cap(*args)
        first = get_hand_camera(43, fps=12)
        second = get_hand_camera(43, fps=12)
        self.assertIs(first, second)
        try:
            with mock.patch.dict(sys.modules, {"cv2": stub}):
                self.assertTrue(first.start())
                self.assertTrue(second.start())
                self.assertEqual(first.status()["leases"], 2)
                first.stop()
                self.assertTrue(first.running)
                second.stop()
            self.assertFalse(first.running)
        finally:
            first.stop()
            second.stop()

    def test_read_loss_recovers_boundedly_instead_of_dying(self):
        """A run of failed reads must re-open the camera, not end the session.

        Previously the capture loop gave up permanently after ten failed
        reads, so one transient glitch killed every hand gesture until DUDE
        was restarted by hand. Recovery must happen, and must stay bounded so
        a genuinely dead device cannot cause a reopen storm.
        """
        from core.orchestrator import hand_tracking as ht
        from core.orchestrator.hand_tracking import get_hand_camera

        class NoFrame:
            def isOpened(self):
                return True
            def read(self):
                return False, None
            def release(self):
                pass

        opens = []

        def _cap(*args):
            opens.append(args)
            return NoFrame()

        stub = mock.Mock()
        stub.CAP_MSMF = 1400
        stub.CAP_DSHOW = 700
        stub.VideoCapture = _cap
        source = get_hand_camera(44, fps=100)
        # Keep the backoff negligible so the test does not sleep for the
        # production recovery interval.
        with mock.patch.object(ht.HandCamera, "RECOVER_BACKOFF_SECONDS", 0.01), \
                mock.patch.object(ht.HandCamera, "RECOVER_ATTEMPTS", 3), \
                mock.patch.dict(sys.modules, {"cv2": stub}):
            self.assertTrue(source.start())
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if source.camera_state == "UNAVAILABLE":
                    break
                time.sleep(0.05)
            # It must have tried to re-open rather than simply dying.
            self.assertGreaterEqual(
                len(opens), 2,
                "camera must attempt a re-open after read loss")
            # Bounded: initial open plus at most RECOVER_ATTEMPTS re-opens.
            self.assertLessEqual(len(opens), 1 + 3)
            # Truthful terminal state once recovery is exhausted.
            self.assertEqual(source.camera_state, "UNAVAILABLE")
            self.assertFalse(source.running)
            self.assertEqual(source.status()["leases"], 0)
            # And a later explicit start still works.
            self.assertTrue(source.start())
        source.stop()

    def test_camera_failure_is_bounded_and_does_not_reopen(self):
        class Dead:
            def __init__(self, *args):
                raise RuntimeError("device busy")

        stub = mock.Mock()
        stub.CAP_MSMF = 1400
        stub.CAP_DSHOW = 700
        stub.VideoCapture = Dead
        camera = HandCamera(index=42)
        with mock.patch.dict(sys.modules, {"cv2": stub}):
            self.assertFalse(camera.start())
            self.assertEqual(camera.camera_state, "TEMPORARILY_UNAVAILABLE")
            self.assertTrue(camera.last_error)
            self.assertEqual(camera.frames_seen, 0)
            self.assertIsNone(camera.latest()[0])

    def test_capability_bus_exposes_owner_truth(self):
        tracker = mock.Mock()
        tracker.status.return_value = {
            "state": "AVAILABLE",
            "camera_state": "AVAILABLE_SHARED",
            "capture_backend": "MSMF",
            "shared_mode": True,
            "two_hand_tracking": True,
        }
        bus = CapabilityBus()
        bus.attach(hand_tracker=tracker)
        result = bus.request("HAND_STATUS")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["camera_state"], "AVAILABLE_SHARED")
        self.assertTrue(result["data"]["two_hand_tracking"])


if __name__ == "__main__":
    unittest.main()
