"""Headless unit tests for TaskEngine unexpected-dialog recovery (4B/4G).

Proves, without a desktop, that when fresh perception shows a
DISMISS-posture dialog (single-OK error box), _do_recovering answers it
with Escape through the production ActionExecutor and returns to
OBSERVING instead of failing blindly. Unknown dialogs must NOT be
touched: the engine falls through to the normal recovery decision.
"""
import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from core.orchestrator.state import (
    ControlInfo,
    OrchestratorState,
    PerceptionSnapshot,
    TaskState,
    TaskType,
)
from core.orchestrator.task_engine import TaskEngine


def _error_snapshot():
    return PerceptionSnapshot(
        active_app="Notepad.exe",
        active_window={"title": "Notepad", "app": "Notepad.exe"},
        controls=[
            ControlInfo(ctype="TextControl",
                        name="The file name is not valid."),
            ControlInfo(ctype="ButtonControl", name="OK", x=1, y=2,
                        w=3, h=4),
        ],
    )


def _plain_snapshot():
    return PerceptionSnapshot(
        active_app="Notepad.exe",
        active_window={"title": "doc - Notepad", "app": "Notepad.exe"},
        controls=[
            ControlInfo(ctype="ButtonControl", name="Bold (Ctrl+B)"),
        ],
    )


class _FakeUIA:
    """Stand-in for uiautomation: focus queries fail, so the focus guard
    (best-effort by design) skips and the test stays deterministic on
    machines where real UIA would report whatever window is frontmost."""

    class _Ctx:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False

    @staticmethod
    def UIAutomationInitializerInThread():
        return _FakeUIA._Ctx()

    @staticmethod
    def GetFocusedControl():
        raise RuntimeError("no UIA in unit test")


def _engine_with(snapshot):
    from unittest.mock import AsyncMock
    from core.orchestrator.action_executor import ActionExecutor
    perception = Mock()
    perception.observe = Mock(return_value=snapshot)
    action_executor = ActionExecutor(perception=perception, memory=None)
    recovery = Mock()
    recovery.decide = AsyncMock(return_value=Mock(
        should_retry=False, reason="unit", action="none"))
    engine = TaskEngine(
        intelligence=Mock(),
        perception=perception,
        action_executor=action_executor,
        verification=Mock(),
        recovery=recovery,
        memory=None,
    )
    ts = TaskState(goal="unit goal", goal_type=TaskType.AUTOMATE)
    engine._task_state = ts
    engine._state = OrchestratorState.RECOVERING
    return engine


class TestDialogDismissRecovery(unittest.TestCase):
    def test_error_dialog_dismissed_and_retried(self):
        import sys
        engine = _engine_with(_error_snapshot())
        real_uia = sys.modules.get('uiautomation')
        sys.modules['uiautomation'] = _FakeUIA
        try:
            with patch('core.orchestrator.action_executor.execute_tool') as mock_tool:
                mock_tool.return_value = 'Pressed escape'
                asyncio.run(engine._do_recovering())
        finally:
            if real_uia is not None:
                sys.modules['uiautomation'] = real_uia
            else:
                del sys.modules['uiautomation']
        self.assertEqual(engine.state, OrchestratorState.OBSERVING)
        phases = [e.get('phase')
                  for e in engine.task_state.recovery_history]
        self.assertIn('dialog_classification', phases)
        self.assertIn('dialog_dismiss', phases)
        kinds = [e.get('kind')
                 for e in engine.task_state.recovery_history
                 if e.get('phase') == 'dialog_classification']
        self.assertIn('error_message', kinds)
        # The dismissal went through the hotkey tool, not a raw click.
        sent = [json.loads(c.args[1]) for c in mock_tool.call_args_list]
        self.assertTrue(any('escape' in str(a.values()) for a in sent))

    def test_no_dialog_falls_through_to_decision(self):
        import sys
        engine = _engine_with(_plain_snapshot())
        real_uia = sys.modules.get('uiautomation')
        sys.modules['uiautomation'] = _FakeUIA
        try:
            with patch('core.orchestrator.action_executor.execute_tool') as mock_tool:
                mock_tool.return_value = 'Pressed escape'
                asyncio.run(engine._do_recovering())
        finally:
            if real_uia is not None:
                sys.modules['uiautomation'] = real_uia
            else:
                del sys.modules['uiautomation']
        # No DISMISS-posture dialog: normal decision path (no retry here).
        self.assertEqual(engine.state, OrchestratorState.FAILED)
        phases = [e.get('phase')
                  for e in engine.task_state.recovery_history]
        self.assertIn('dialog_classification', phases)
        self.assertNotIn('dialog_dismiss', phases)
        mock_tool.assert_not_called()


if __name__ == '__main__':
    unittest.main()
