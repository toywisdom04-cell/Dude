"""Phase 16 spatial-object and contextual-pointer tests."""
import unittest
from types import SimpleNamespace

from core.capability_bus import CapabilityBus
from core.orchestrator.hand_tracking import (
    Gesture,
    HandObservation,
    HandState,
)
from core.orchestrator.spatial_hand_interaction import (
    SpatialInteractionLayer,
    SpatialObject,
)


class _Mapper:
    def map(self, x, y):
        return (int(x * 1000), int(y * 1000))


def _hand(hand_id, label, tip, gesture=Gesture.OPEN, frame_id=1):
    lm = [(tip[0], tip[1])] * 21
    return HandObservation(
        label=label,
        identity=hand_id,
        palm=tip,
        wrist=tip,
        landmarks=tuple(lm),
        landmarks_3d=tuple((x, y, 0.0) for x, y in lm),
        gesture=gesture,
        gesture_confidence=0.95,
        handedness_score=0.95,
    )


class TestPhase16SpatialInteraction(unittest.TestCase):
    def test_two_hands_keep_independent_targets_and_hide_desktop_pointer(self):
        layer = SpatialInteractionLayer()
        layer.register_object(SpatialObject("left_object", x=0.25, y=0.5,
                                             width=0.20, height=0.30))
        layer.register_object(SpatialObject("right_object", x=0.75, y=0.5,
                                             width=0.20, height=0.30))
        state = HandState(hands=(
            _hand("hand_0", "left", (0.25, 0.5)),
            _hand("hand_1", "right", (0.75, 0.5)),
        ), frame_id=1)
        snapshot = layer.update(
            state,
            stable_by_hand={
                "hand_0": (Gesture.OPEN, 4),
                "hand_1": (Gesture.OPEN, 4),
            },
            screen_mapper=_Mapper(), now=1.0)
        self.assertEqual(snapshot["world"], "spatial")
        self.assertEqual(snapshot["pointers"], [])
        self.assertEqual({h["target_id"] for h in snapshot["hands"]},
                         {"left_object", "right_object"})

    def test_screen_pointer_is_visible_only_outside_spatial_hit(self):
        layer = SpatialInteractionLayer()
        layer.register_object(SpatialObject("object", x=0.25, y=0.5,
                                             width=0.20, height=0.30))
        screen = layer.update(
            HandState(hands=(_hand("hand_0", "left", (0.80, 0.5)),), frame_id=1),
            stable_by_hand={"hand_0": (Gesture.POINTING, 4)},
            screen_mapper=_Mapper(), now=1.0)
        self.assertEqual(screen["world"], "screen")
        self.assertEqual(len(screen["pointers"]), 1)
        self.assertEqual(screen["pointers"][0]["screen_xy"], (800, 500))
        self.assertEqual(screen["pointers"][0]["world"], "screen")

    def test_independent_hand_worlds_can_be_mixed(self):
        layer = SpatialInteractionLayer()
        layer.register_object(SpatialObject("object", x=0.25, y=0.5,
                                             width=0.20, height=0.30))
        state = HandState(hands=(
            _hand("hand_0", "left", (0.25, 0.5), Gesture.PINCH),
            _hand("hand_1", "right", (0.80, 0.5), Gesture.POINTING),
        ), frame_id=1)
        snapshot = layer.update(state, stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
            "hand_1": (Gesture.POINTING, 4),
        }, screen_mapper=_Mapper(), now=1.0)
        self.assertEqual(snapshot["world"], "mixed")
        self.assertEqual(len(snapshot["pointers"]), 1)
        self.assertEqual(snapshot["hands"][0]["target_id"], "object")

    def test_touch_grab_move_release_updates_virtual_object(self):
        layer = SpatialInteractionLayer()
        obj = layer.register_object(SpatialObject(
            "object", x=0.30, y=0.50, width=0.24, height=0.30))
        first = HandState(hands=(
            _hand("hand_0", "left", (0.30, 0.5), Gesture.PINCH),), frame_id=1)
        layer.update(first, stable_by_hand={"hand_0": (Gesture.PINCH, 4)},
                     now=1.0)
        self.assertTrue(any(e["name"] == "grab" for e in layer.events()))
        second = HandState(hands=(
            _hand("hand_0", "left", (0.36, 0.5), Gesture.PINCH),), frame_id=2)
        layer.update(second, stable_by_hand={"hand_0": (Gesture.PINCH, 4)},
                     now=1.1)
        self.assertAlmostEqual(obj.x, 0.36, places=3)
        release = HandState(hands=(
            _hand("hand_0", "left", (0.36, 0.5), Gesture.OPEN),), frame_id=3)
        layer.update(release, stable_by_hand={"hand_0": (Gesture.OPEN, 4)},
                     now=1.6)
        self.assertTrue(any(e["name"] == "release" for e in layer.events()))

    def test_grab_survives_brief_pinch_dropout_before_release(self):
        layer = SpatialInteractionLayer()
        layer.register_object(SpatialObject(
            "object", x=0.50, y=0.50, width=0.30, height=0.30))
        first = HandState(hands=(
            _hand("hand_0", "right", (0.50, 0.5), Gesture.PINCH),), frame_id=1)
        layer.update(first, stable_by_hand={
            "hand_0": (Gesture.PINCH, 4)}, now=1.0)
        held = HandState(hands=(
            _hand("hand_0", "right", (0.50, 0.5), Gesture.OPEN),), frame_id=2)
        during_grace = layer.update(held, stable_by_hand={
            "hand_0": (Gesture.OPEN, 4)}, now=1.10)
        self.assertTrue(during_grace["hands"][0]["grabbed"])
        released = layer.update(held, stable_by_hand={
            "hand_0": (Gesture.OPEN, 4)}, now=1.60)
        self.assertFalse(released["hands"][0]["grabbed"])

    def test_grab_survives_a_brief_detector_dropout(self):
        layer = SpatialInteractionLayer()
        layer.register_object(SpatialObject(
            "object", x=0.50, y=0.50, width=0.30, height=0.30))
        layer.update(HandState(hands=(
            _hand("hand_0", "right", (0.50, 0.5), Gesture.PINCH),), frame_id=1),
            stable_by_hand={"hand_0": (Gesture.PINCH, 4)}, now=4.0)
        held = layer.update(HandState(hands=(), frame_id=2), now=4.10)
        self.assertTrue(held["hands"][0]["grabbed"])
        rejoined = layer.update(HandState(hands=(
            _hand("hand_0", "right", (0.52, 0.5), Gesture.PINCH),), frame_id=3),
            stable_by_hand={"hand_0": (Gesture.PINCH, 4)}, now=4.20)
        self.assertTrue(rejoined["hands"][0]["grabbed"])
        self.assertEqual(rejoined["hands"][0]["target_id"], "object")
        layer.update(HandState(hands=(), frame_id=4), now=4.80)
        self.assertTrue(any(e["name"] == "release"
                            for e in layer.events(64)))

    def test_stale_pinch_vote_cannot_keep_a_released_object_attached(self):
        layer = SpatialInteractionLayer()
        layer.register_object(SpatialObject(
            "object", x=0.50, y=0.50, width=0.30, height=0.30))
        layer.update(HandState(hands=(
            _hand("hand_0", "right", (0.50, 0.5), Gesture.PINCH),), frame_id=1),
            stable_by_hand={"hand_0": (Gesture.PINCH, 4)}, now=8.0)
        separated = [(0.5, 0.5)] * 21
        separated[4] = (0.35, 0.5)
        open_hand = HandObservation(
            label="right", identity="hand_0", palm=(0.5, 0.5),
            wrist=(0.5, 0.5), landmarks=tuple(separated),
            gesture=Gesture.OPEN, gesture_confidence=0.9,
            handedness_score=0.9)
        held = layer.update(HandState(hands=(open_hand,), frame_id=2),
                            stable_by_hand={"hand_0": (Gesture.PINCH, 4)},
                            now=8.1)
        self.assertTrue(held["hands"][0]["grabbed"])
        released = layer.update(HandState(hands=(open_hand,), frame_id=3),
                                stable_by_hand={"hand_0": (Gesture.PINCH, 4)},
                                now=8.6)
        self.assertFalse(released["hands"][0]["grabbed"])

    def test_claw_holds_and_moves_a_virtual_object_without_scaling(self):
        layer = SpatialInteractionLayer()
        obj = layer.register_object(SpatialObject(
            "object", x=0.50, y=0.50, width=0.30, height=0.30))
        layer.update(HandState(hands=(
            _hand("hand_0", "right", (0.50, 0.5), Gesture.CLAW),), frame_id=1),
            stable_by_hand={"hand_0": (Gesture.CLAW, 4)}, now=9.0)
        moved = layer.update(HandState(hands=(
            _hand("hand_0", "right", (0.60, 0.5), Gesture.CLAW),), frame_id=2),
            stable_by_hand={"hand_0": (Gesture.CLAW, 4)}, now=9.1)
        self.assertTrue(moved["hands"][0]["grabbed"])
        self.assertAlmostEqual(obj.x, 0.60, places=3)
        self.assertAlmostEqual(obj.scale, 1.0, places=3)
        self.assertEqual(moved["pointers"], [])

    def test_one_hand_move_does_not_scale(self):
        layer = SpatialInteractionLayer()
        obj = layer.register_object(SpatialObject(
            "object", x=0.50, y=0.50, width=0.30, height=0.30))
        layer.update(HandState(hands=(
            _hand("hand_0", "right", (0.40, 0.5), Gesture.PINCH),), frame_id=1),
            stable_by_hand={"hand_0": (Gesture.PINCH, 4)}, now=5.0)
        layer.update(HandState(hands=(
            _hand("hand_0", "right", (0.50, 0.5), Gesture.PINCH),), frame_id=2),
            stable_by_hand={"hand_0": (Gesture.PINCH, 4)}, now=5.1)
        self.assertAlmostEqual(obj.x, 0.60, places=3)
        self.assertAlmostEqual(obj.scale, 1.0, places=3)

    def test_two_hand_scale_moves_object_with_the_hand_midpoint(self):
        layer = SpatialInteractionLayer()
        obj = layer.register_object(SpatialObject(
            "object", x=0.50, y=0.50, width=0.30, height=0.30))
        layer.update(HandState(hands=(
            _hand("hand_0", "left", (0.40, 0.5), Gesture.PINCH),
            _hand("hand_1", "right", (0.60, 0.5), Gesture.PINCH),
        ), frame_id=1), stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
            "hand_1": (Gesture.PINCH, 4),
        }, now=6.0)
        layer.update(HandState(hands=(
            _hand("hand_0", "left", (0.30, 0.5), Gesture.PINCH),
            _hand("hand_1", "right", (0.70, 0.5), Gesture.PINCH),
        ), frame_id=2), stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
            "hand_1": (Gesture.PINCH, 4),
        }, now=6.1)
        self.assertGreater(obj.scale, 1.0)
        self.assertAlmostEqual(obj.x, 0.50, places=3)
        self.assertAlmostEqual(obj.y, 0.50, places=3)
        self.assertTrue(any(e["name"] == "scale" for e in layer.events()))

    def test_releasing_second_hand_returns_to_one_hand_move(self):
        layer = SpatialInteractionLayer()
        obj = layer.register_object(SpatialObject(
            "object", x=0.50, y=0.50, width=0.30, height=0.30))
        layer.update(HandState(hands=(
            _hand("hand_0", "left", (0.40, 0.5), Gesture.PINCH),
            _hand("hand_1", "right", (0.60, 0.5), Gesture.PINCH),
        ), frame_id=1), stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
            "hand_1": (Gesture.PINCH, 4),
        }, now=7.0)
        # The second hand is still inside the release grace window, but it
        # is no longer an active pinch and must not scale the first hand.
        layer.update(HandState(hands=(
            _hand("hand_0", "left", (0.40, 0.5), Gesture.PINCH),
            _hand("hand_1", "right", (0.60, 0.5), Gesture.OPEN),
        ), frame_id=2), stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
            "hand_1": (Gesture.OPEN, 4),
        }, now=7.1)
        layer.update(HandState(hands=(
            _hand("hand_0", "left", (0.30, 0.5), Gesture.PINCH),
        ), frame_id=3), stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
        }, now=7.2)
        self.assertAlmostEqual(obj.x, 0.40, places=3)
        self.assertAlmostEqual(obj.scale, 1.0, places=3)

    def test_second_hand_can_join_near_object_for_scaling(self):
        layer = SpatialInteractionLayer()
        obj = layer.register_object(SpatialObject(
            "object", x=0.50, y=0.50, width=0.30, height=0.30))
        joined = layer.update(HandState(hands=(
            _hand("hand_0", "left", (0.40, 0.5), Gesture.PINCH),
            # Just outside the animated right edge, but within the
            # co-grab tolerance of the first hand's grabbed object.
            _hand("hand_1", "right", (0.68, 0.5), Gesture.PINCH),
        ), frame_id=1), stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
            "hand_1": (Gesture.PINCH, 4),
        }, now=3.0)
        self.assertEqual(joined["hands"][1]["target_id"], "object")
        self.assertTrue(joined["hands"][1]["grabbed"])
        spread = layer.update(HandState(hands=(
            _hand("hand_0", "left", (0.20, 0.5), Gesture.PINCH),
            _hand("hand_1", "right", (0.80, 0.5), Gesture.PINCH),
        ), frame_id=2), stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
            "hand_1": (Gesture.PINCH, 4),
        }, now=3.1)
        self.assertGreater(obj.scale, 1.0)
        self.assertAlmostEqual(obj.x, 0.46, places=3)
        self.assertAlmostEqual(obj.y, 0.50, places=3)
        self.assertEqual(spread["pointers"], [])

    def test_two_hand_spatial_scale_and_independent_disappearance(self):
        layer = SpatialInteractionLayer()
        obj = layer.register_object(SpatialObject(
            "object", x=0.50, y=0.50, width=0.30, height=0.30))
        both = HandState(hands=(
            _hand("hand_0", "left", (0.40, 0.5), Gesture.PINCH),
            _hand("hand_1", "right", (0.60, 0.5), Gesture.PINCH),
        ), frame_id=1)
        layer.update(both, stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
            "hand_1": (Gesture.PINCH, 4),
        }, now=2.0)
        spread = HandState(hands=(
            _hand("hand_0", "left", (0.30, 0.5), Gesture.PINCH),
            _hand("hand_1", "right", (0.70, 0.5), Gesture.PINCH),
        ), frame_id=2)
        snapshot = layer.update(spread, stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
            "hand_1": (Gesture.PINCH, 4),
        }, now=2.1)
        self.assertGreater(obj.scale, 1.0)
        self.assertEqual(snapshot["world"], "spatial")
        self.assertTrue(any(e["name"] == "scale" for e in layer.events()))

        one = HandState(hands=(
            _hand("hand_0", "left", (0.30, 0.5), Gesture.PINCH),), frame_id=3)
        after = layer.update(one, stable_by_hand={
            "hand_0": (Gesture.PINCH, 4),
        }, now=2.7)
        self.assertEqual(len(after["hands"]), 1)
        self.assertEqual(after["hands"][0]["hand_id"], "hand_0")

    def test_object_control_hit_emits_semantic_control_event(self):
        layer = SpatialInteractionLayer()
        obj = layer.register_object(SpatialObject(
            "panel", x=0.50, y=0.50, width=0.40, height=0.40,
            controls=(("save", 0.45, 0.45, 0.10, 0.10, "save"),)))
        layer.update(
            HandState(hands=(_hand("hand_0", "left", (0.48, 0.48),
                                   Gesture.PINCH),), frame_id=1),
            stable_by_hand={"hand_0": (Gesture.PINCH, 4)}, now=1.0)
        self.assertTrue(any(e["name"] == "control_touch"
                            for e in layer.events()))
        self.assertEqual(obj.object_id, "panel")

    def test_capability_bus_reports_contextual_spatial_state(self):
        layer = SpatialInteractionLayer()
        bus = CapabilityBus()
        bus.attach(hand_tracker=SimpleNamespace(spatial_layer=layer))
        result = bus.request("SPATIAL_HAND_STATUS")
        self.assertTrue(result["success"])
        self.assertIn("pointers", result["data"])

    def test_virtual_object_layer_has_no_executor_or_direct_input(self):
        layer = SpatialInteractionLayer()
        self.assertFalse(hasattr(layer, "execute"))
        self.assertFalse(hasattr(layer, "action_executor"))
        self.assertFalse(hasattr(layer, "pyautogui"))


if __name__ == "__main__":
    unittest.main()
