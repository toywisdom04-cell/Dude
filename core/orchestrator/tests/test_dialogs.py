"""Headless unit tests for generic dialog classification (Phase 4B).

Snapshots mirror real Windows dialogs observed during Phase 3
(Notepad Save As, Confirm Save As, invalid-name error, Don't-save
prompt). No desktop needed.
"""
import unittest

from core.orchestrator.dialogs import (
    DialogClassification,
    DialogKind,
    DialogPosture,
    classify_dialog,
)
from core.orchestrator.state import ControlInfo, PerceptionSnapshot


def _snap(title, controls, wclass="", app="Notepad.exe"):
    return PerceptionSnapshot(
        active_app=app,
        active_window={"title": title, "app": app, "wclass": wclass},
        controls=controls,
    )


def _btn(name, aid="", x=0, y=0):
    return ControlInfo(ctype="ButtonControl", name=name, automation_id=aid,
                       x=x, y=y, w=64, h=32)


def _edit(name, aid="", x=0, y=0):
    return ControlInfo(ctype="EditControl", name=name, automation_id=aid,
                       x=x, y=y, w=200, h=32)


def _text(name):
    return ControlInfo(ctype="TextControl", name=name)


class TestDialogClassification(unittest.TestCase):
    def test_save_as_by_controls(self):
        snap = _snap("Save as", [
            _edit("File name:", aid="1001", x=474, y=1264),
            _btn("Save", aid="1", x=1959, y=1495),
            _btn("Cancel", aid="2", x=2159, y=1495),
        ], wclass="#32770")
        res = classify_dialog(snap, {"expecting": "save_as"})
        self.assertEqual(res.kind, DialogKind.SAVE_AS)
        self.assertTrue(res.expected)
        self.assertEqual(res.posture, DialogPosture.PROCEED)

    def test_save_as_unexpected_without_context(self):
        snap = _snap("Save as", [
            _edit("File name:", aid="1001"),
            _btn("Save", aid="1"),
            _btn("Cancel", aid="2"),
        ])
        res = classify_dialog(snap, {})
        self.assertEqual(res.kind, DialogKind.SAVE_AS)
        self.assertIsNone(res.expected)

    def test_confirm_overwrite(self):
        snap = _snap("Confirm Save As", [
            _text("OW_File.txt already exists. "
                  "Do you want to replace it?"),
            _btn("Yes", aid="CommandButton_6", x=1476, y=902),
            _btn("No", aid="CommandButton_7", x=1624, y=902),
        ], wclass="#32770")
        res = classify_dialog(snap, {"expecting": "save"})
        self.assertEqual(res.kind, DialogKind.CONFIRM_OVERWRITE)
        self.assertTrue(res.expected)
        self.assertEqual(res.posture, DialogPosture.ANSWER)
        self.assertIn(("Yes", "ButtonControl"), res.answer_controls)

    def test_dont_save_prompt(self):
        snap = _snap("Notepad", [
            _btn("Save"), _btn("Don't save"), _btn("Cancel"),
        ])
        res = classify_dialog(snap, {})
        self.assertEqual(res.kind, DialogKind.DONT_SAVE)
        self.assertEqual(res.posture, DialogPosture.ANSWER)

    def test_error_message_single_ok(self):
        snap = _snap("Notepad", [
            _text("The file name is not valid."),
            _btn("OK", aid="CommandButton_1"),
        ], wclass="#32770")
        res = classify_dialog(snap, {})
        self.assertEqual(res.kind, DialogKind.ERROR_MESSAGE)
        self.assertEqual(res.posture, DialogPosture.DISMISS)

    def test_unknown_modal_aborts(self):
        snap = _snap("Something strange", [
            _btn("Frobnicator"), _btn("Cancel"),
        ], wclass="#32770")
        res = classify_dialog(snap, {"expecting": "save_as"})
        self.assertEqual(res.kind, DialogKind.UNKNOWN_MODAL)
        self.assertEqual(res.posture, DialogPosture.ABORT)
        self.assertFalse(res.expected)

    def test_no_dialog(self):
        snap = _snap("Document - Notepad", [
            _btn("Bold (Ctrl+B)"),
        ])
        res = classify_dialog(snap, {})
        self.assertEqual(res.kind, DialogKind.NONE)
        self.assertEqual(res.posture, DialogPosture.PROCEED)


if __name__ == '__main__':
    unittest.main()
