"""Headless unit tests for Phase 4 planning (no desktop needed).

Covers the Explorer GUI patterns, goal composition, legacy
preservation, and clipboard file-drop verification using fabricated
inputs only.
"""
import sys
import unittest

from core.orchestrator import IntelligenceRouter, ProcedureStore, TaskState
from core.orchestrator.state import PerceptionLevel, PerceptionSnapshot

D = r'C:\Users\duvvu\Desktop\DUDE_Phase4_Test'


def _route(goal):
    router = IntelligenceRouter(
        procedure_store=ProcedureStore(db_path=':memory:'))
    perception = PerceptionSnapshot(
        capture_method=PerceptionLevel.LEVEL_1_APP_WINDOW)
    task_state = TaskState(goal=goal)
    return router.route(task_state, perception, goal)


def _actions(result):
    return [s.action_type for s in result.plan.steps] if result.plan else []


class TestExplorerPatterns(unittest.TestCase):
    def test_open_at(self):
        res = _route('Open File Explorer at %s' % D)
        self.assertEqual(str(res.decision), 'RouteDecision.LOCAL_REASONING')
        self.assertEqual(_actions(res),
                         ['open_app', 'hotkey', 'type_text', 'hotkey',
                          'hotkey'])
        self.assertEqual(res.plan.steps[0].target_description, 'explorer')
        self.assertEqual(res.plan.steps[1].target_description, 'ctrl+l')
        # Focus must leave the address bar or later view-hotkeys die.
        self.assertEqual(res.plan.steps[-1].target_description, 'esc')

    def test_mkdir_with_dir(self):
        res = _route(
            'In File Explorer at %s, create a folder named Phase4T_FolderA'
            % D)
        self.assertEqual(str(res.decision), 'RouteDecision.LOCAL_REASONING')
        self.assertEqual(res.plan.steps[-1].action_type, 'hotkey')
        from core.orchestrator.state import VerificationMethod
        self.assertEqual(
            res.plan.steps[-1].verification_method,
            VerificationMethod.FILE_EXISTS)
        combos = [s.target_description for s in res.plan.steps
                  if s.action_type == 'hotkey']
        self.assertIn('ctrl+shift+n', combos)

    def test_mkdir_without_dir_declines(self):
        res = _route('create a folder named Lonely in Explorer')
        self.assertEqual(str(res.decision), 'RouteDecision.NO_SOLUTION')

    def test_rename(self):
        res = _route('rename Phase4T_FolderA to Phase4T_FolderB in %s' % D)
        self.assertEqual(str(res.decision), 'RouteDecision.LOCAL_REASONING')
        targets = [s.target_description for s in res.plan.steps]
        self.assertIn('Phase4T_FolderA | ListItemControl', targets)
        combos = [s.target_description for s in res.plan.steps
                  if s.action_type == 'hotkey']
        self.assertIn('f2', combos)

    def test_move(self):
        res = _route('move Phase4T_FolderB from %s to %s\\Archive' % (D, D))
        self.assertEqual(str(res.decision), 'RouteDecision.LOCAL_REASONING')
        combos = [s.target_description for s in res.plan.steps
                  if s.action_type == 'hotkey']
        self.assertIn('ctrl+x', combos)
        self.assertIn('ctrl+v', combos)
        # Window-reuse rule: one open per task, navigations share it.
        opens = [s for s in res.plan.steps
                 if s.action_type == 'open_app']
        self.assertEqual(len(opens), 1)

    def test_legacy_bare_create_preserved(self):
        res = _route('create folder Reports')
        self.assertEqual(str(res.decision),
                         'RouteDecision.DETERMINISTIC_SKILL')

    def test_composition_concatenates(self):
        res = _route(
            "In Notepad, type 'M1', save it as M1.txt in %s, then "
            "in File Explorer at %s, create a folder named Sub" % (D, D))
        self.assertEqual(str(res.decision), 'RouteDecision.LOCAL_REASONING')
        actions = _actions(res)
        self.assertIn('open_app', actions)
        self.assertIn('click', actions)
        # both app opens present: Notepad first, explorer later
        opens = [s.target_description for s in res.plan.steps
                 if s.action_type == 'open_app']
        self.assertEqual(opens[0], 'Notepad')
        self.assertIn('explorer', opens[1:])

    def test_composition_fails_cleanly_when_clause_unknown(self):
        res = _route('Open Comet and click the "X" button, then '
                     'teleport to Mars')
        self.assertEqual(str(res.decision), 'RouteDecision.NO_SOLUTION')


class TestFocusedControlVerification(unittest.TestCase):
    def test_focus_match(self):
        import asyncio
        from core.orchestrator.verification import VerificationEngine
        from core.orchestrator.state import (
            Action, ControlInfo, ExpectedResult, TargetSpec,
            VerificationMethod)
        eng = VerificationEngine(perception=None)
        snap = PerceptionSnapshot(
            controls=[],
            focused_control=ControlInfo(
                ctype="EditControl", name="Address Bar"),
        )
        action = Action(
            task_id='t', step_id=0, intent='focus', action_type='hotkey',
            target=TargetSpec(text_match='ctrl+l'),
            target_description='ctrl+l',
            expected_result=ExpectedResult(text_expected='Address Bar'),
            verification_method=VerificationMethod.FOCUSED_CONTROL)
        res = asyncio.run(eng._verify_focused(
            action, action.expected_result, snap))
        self.assertTrue(res.success)

    def test_focus_mismatch_fails(self):
        import asyncio
        from core.orchestrator.verification import VerificationEngine
        from core.orchestrator.state import (
            Action, ControlInfo, ExpectedResult, TargetSpec,
            VerificationMethod)
        eng = VerificationEngine(perception=None)
        snap = PerceptionSnapshot(
            controls=[],
            focused_control=ControlInfo(
                ctype="EditControl", name="Search Box"),
        )
        action = Action(
            task_id='t', step_id=0, intent='focus', action_type='hotkey',
            target=TargetSpec(text_match='ctrl+l'),
            target_description='ctrl+l',
            expected_result=ExpectedResult(text_expected='Address Bar'),
            verification_method=VerificationMethod.FOCUSED_CONTROL)
        res = asyncio.run(eng._verify_focused(
            action, action.expected_result, snap))
        self.assertFalse(res.success)

    def test_no_focus_observed_fails(self):
        import asyncio
        from core.orchestrator.verification import VerificationEngine
        from core.orchestrator.state import (
            Action, ExpectedResult, TargetSpec, VerificationMethod)
        eng = VerificationEngine(perception=None)
        snap = PerceptionSnapshot(controls=[])
        action = Action(
            task_id='t', step_id=0, intent='focus', action_type='hotkey',
            target=TargetSpec(text_match='ctrl+l'),
            target_description='ctrl+l',
            expected_result=ExpectedResult(text_expected='Address Bar'),
            verification_method=VerificationMethod.FOCUSED_CONTROL)
        res = asyncio.run(eng._verify_focused(
            action, action.expected_result, snap))
        self.assertFalse(res.success)


class TestClipboardFileDrop(unittest.TestCase):
    def test_hdrop_path_matches(self):
        import asyncio
        from core.orchestrator.verification import VerificationEngine
        from core.orchestrator.state import (
            Action, ExpectedResult, TargetSpec, VerificationMethod)

        class FakeClip:
            CF_UNICODETEXT = 1
            CF_HDROP = 2

            @staticmethod
            def OpenClipboard():
                pass

            @staticmethod
            def CloseClipboard():
                pass

            @staticmethod
            def GetClipboardData(fmt):
                if fmt == 1:
                    raise Exception('not available')
                return ('C:\\Users\\duvvu\\Desktop\\DUDE_Phase4_Test\\'
                        'Phase4T_FolderB',)

        real = sys.modules.get('win32clipboard')
        sys.modules['win32clipboard'] = FakeClip
        try:
            eng = VerificationEngine(perception=None)
            action = Action(
                task_id='t', step_id=0, intent='cut', action_type='hotkey',
                target=TargetSpec(text_match='ctrl+x'),
                target_description='ctrl+x',
                expected_result=ExpectedResult(
                    text_expected='Phase4T_FolderB'),
                verification_method=VerificationMethod.CLIPBOARD_CONTENT)
            res = asyncio.run(eng._verify_clipboard(
                action, action.expected_result))
            self.assertTrue(res.success)
        finally:
            if real is not None:
                sys.modules['win32clipboard'] = real
            else:
                del sys.modules['win32clipboard']


if __name__ == '__main__':
    unittest.main()
