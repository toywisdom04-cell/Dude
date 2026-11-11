"""Headless unit tests for generic UI grounding (Phase 4A).

Proves the application-independent grounding contract without a desktop:
UIA (id, then name) -> OCR -> relative -> absolute, role-as-hard-constraint,
and disabled/invisible exclusion. Uses fabricated perception snapshots only.
"""
import unittest

from core.orchestrator.action_executor import ActionExecutor
from core.orchestrator.state import (
    Action,
    ControlInfo,
    GroundingMethod,
    OCRRegion,
    PerceptionSnapshot,
    TargetSpec,
)


def _action(target, action_type="click"):
    from core.orchestrator.state import ExpectedResult, RiskLevel
    from core.orchestrator.state import VerificationMethod
    return Action(
        task_id="t",
        step_id=0,
        intent="test",
        action_type=action_type,
        target=target,
        target_description="test target",
        grounding_method=GroundingMethod.UIA,
        expected_result=ExpectedResult(),
        verification_method=VerificationMethod.CUSTOM,
        risk_level=RiskLevel.LOW,
    )


class TestGroundingPriority(unittest.TestCase):
    def setUp(self):
        self.ex = ActionExecutor.__new__(ActionExecutor)
        self.ex.min_confidence = 0.7

    def test_uia_control_id_wins(self):
        perception = PerceptionSnapshot(controls=[
            ControlInfo(ctype="ButtonControl", name="Save", automation_id="1",
                        x=10, y=10, w=20, h=20),
        ])
        g = self.ex._ground_action(
            _action(TargetSpec(control_id="1")), perception)
        self.assertIsNotNone(g.control)
        self.assertEqual(g.method, GroundingMethod.UIA)
        self.assertAlmostEqual(g.confidence, 0.95)
        self.assertEqual(g.coordinates, (20, 20))

    def test_uia_name_unique_match(self):
        perception = PerceptionSnapshot(controls=[
            ControlInfo(ctype="ButtonControl", name="Save", x=10, y=10,
                        w=20, h=20, role="ButtonControl"),
        ])
        g = self.ex._ground_action(
            _action(TargetSpec(control_name="Save")), perception)
        self.assertIsNotNone(g.control)
        self.assertEqual(g.method, GroundingMethod.UIA)

    def test_role_is_hard_constraint(self):
        # "Save" exists only as a TreeControl here: a ButtonControl
        # request must NOT ground to it.
        perception = PerceptionSnapshot(controls=[
            ControlInfo(ctype="TreeControl", name="Save Fields", x=10,
                        y=10, w=20, h=20, role="TreeControl"),
        ])
        g = self.ex._ground_action(
            _action(TargetSpec(control_name="Save",
                               control_role="ButtonControl")),
            perception)
        self.assertIsNone(g.control)

    def test_role_disambiguates_repeated_names(self):
        perception = PerceptionSnapshot(controls=[
            ControlInfo(ctype="ComboBoxControl", name="File name:",
                        x=10, y=10, w=20, h=20, role="ComboBoxControl"),
            ControlInfo(ctype="EditControl", name="File name:",
                        x=10, y=40, w=20, h=20, role="EditControl"),
        ])
        g = self.ex._ground_action(
            _action(TargetSpec(control_name="File name:",
                               control_role="EditControl")),
            perception)
        self.assertIsNotNone(g.control)
        # Role pre-filtering leaves exactly one candidate, grounded
        # with top UIA confidence at the Edit's center.
        self.assertEqual(g.method, GroundingMethod.UIA)
        self.assertEqual(g.coordinates, (20, 50))

    def test_disabled_and_invisible_excluded(self):
        perception = PerceptionSnapshot(controls=[
            ControlInfo(ctype="ButtonControl", name="Save", x=10, y=10,
                        w=20, h=20, enabled=False, role="ButtonControl"),
            ControlInfo(ctype="ButtonControl", name="Save", x=10, y=40,
                        w=20, h=20, visible=False, role="ButtonControl"),
        ])
        g = self.ex._ground_action(
            _action(TargetSpec(control_name="Save",
                               control_role="ButtonControl")),
            perception)
        self.assertIsNone(g.control)

    def test_ocr_fallback_without_uia_match(self):
        perception = PerceptionSnapshot(
            controls=[],
            ocr_regions=[OCRRegion(text="hello world", x=5, y=5, w=10,
                                   h=10, confidence=0.9)],
        )
        g = self.ex._ground_action(
            _action(TargetSpec(text_match="hello")), perception)
        self.assertIsNotNone(g.control)
        self.assertEqual(g.method, GroundingMethod.OCR_TEXT)

    def test_uia_beats_ocr(self):
        perception = PerceptionSnapshot(
            controls=[ControlInfo(ctype="ButtonControl", name="hello",
                                  x=1, y=2, w=3, h=4,
                                  role="ButtonControl")],
            ocr_regions=[OCRRegion(text="hello", x=5, y=5, w=10, h=10,
                                   confidence=0.9)],
        )
        g = self.ex._ground_action(
            _action(TargetSpec(control_name="hello",
                               text_match="hello")),
            perception)
        self.assertEqual(g.method, GroundingMethod.UIA)

    def test_no_match_grounds_nothing(self):
        perception = PerceptionSnapshot(controls=[])
        g = self.ex._ground_action(
            _action(TargetSpec(control_name="Nope")), perception)
        self.assertIsNone(g.control)
        self.assertEqual(g.confidence, 0.0)


if __name__ == '__main__':
    unittest.main()
