"""Headless unit tests for the application instance manager (Phase 5).

No desktop needed: window enumeration is stubbed per test; only the
decision policy, ownership bookkeeping, and background classification
are exercised.
"""
import unittest

from core.orchestrator.app_instances import (
    AppInstanceManager,
    ReuseDecision,
    WindowInfo,
    _exe_names_for,
    app_identity_matches,
    classify_background,
    is_app_window,
)
from core.orchestrator.state import TaskState


def _mgr_with(windows):
    mgr = AppInstanceManager()
    mgr.snapshot = lambda app_names=None: list(windows)
    return mgr


def _win(hwnd, exe="notepad.exe", title="doc", responsive=True):
    return WindowInfo(hwnd=hwnd, pid=1000 + hwnd, exe=exe, title=title,
                      wclass="Notepad", visible=True,
                      responsive=responsive)


class TestExeMapping(unittest.TestCase):
    def test_known_apps(self):
        self.assertIn("notepad.exe", _exe_names_for("Notepad"))
        self.assertIn("explorer.exe", _exe_names_for("File Explorer"))
        self.assertIn("comet.exe", _exe_names_for("comet"))

    def test_unknown_app_falls_back(self):
        self.assertEqual(_exe_names_for("WordPad"), ["wordpad.exe"])
        self.assertEqual(_exe_names_for(""), [])


class TestResolve(unittest.TestCase):
    def test_not_found_when_empty(self):
        res = _mgr_with([]).resolve("notepad", TaskState())
        self.assertEqual(res.decision, ReuseDecision.NOT_FOUND)
        self.assertIsNone(res.window)

    def test_reuse_single_window(self):
        mgr = _mgr_with([_win(11)])
        res = mgr.resolve("notepad", TaskState())
        self.assertEqual(res.decision, ReuseDecision.REUSE_EXISTING)
        self.assertEqual(res.window.hwnd, 11)
        self.assertEqual(res.candidates_considered, 1)

    def test_owned_window_preferred(self):
        ts = TaskState()
        ts.owned_hwnds = {22}
        mgr = _mgr_with([_win(11), _win(22)])
        res = mgr.resolve("notepad", ts)
        self.assertEqual(res.window.hwnd, 22)
        self.assertIn("owned", res.reason)

    def test_unresponsive_windows_unsafe(self):
        mgr = _mgr_with([_win(11, responsive=False)])
        res = mgr.resolve("notepad", TaskState())
        self.assertEqual(res.decision, ReuseDecision.UNSAFE_TO_REUSE)
        self.assertIsNone(res.window)

    def test_unresponsive_skipped_when_usable_exists(self):
        mgr = _mgr_with([_win(11, responsive=False), _win(22)])
        res = mgr.resolve("notepad", TaskState())
        self.assertEqual(res.window.hwnd, 22)

    def test_lowest_hwnd_is_stable_fallback(self):
        mgr = _mgr_with([_win(99), _win(11)])
        res = mgr.resolve("notepad", TaskState())
        self.assertEqual(res.window.hwnd, 11)

    def test_require_independent_opens_new(self):
        mgr = _mgr_with([_win(11)])
        res = mgr.resolve("notepad", TaskState(),
                          require_independent=True)
        self.assertEqual(res.decision, ReuseDecision.OPEN_NEW)
        self.assertIsNone(res.window)

    def test_decision_recorded_on_task(self):
        ts = TaskState()
        mgr = _mgr_with([_win(11)])
        res = mgr.resolve("notepad", ts)
        AppInstanceManager.record(ts, "notepad", res)
        self.assertEqual(len(ts.window_decisions), 1)
        self.assertEqual(ts.window_decisions[0]["decision"],
                         "reuse_existing")
        self.assertEqual(ts.window_decisions[0]["hwnd"], 11)

    def test_mark_owned(self):
        ts = TaskState()
        AppInstanceManager.mark_owned(ts, 42)
        self.assertIn(42, ts.owned_hwnds)


class TestIsAppWindow(unittest.TestCase):
    def test_explorer_needs_cabinet_class(self):
        self.assertTrue(is_app_window(
            "explorer.exe", "CabinetWClass", "Documents"))
        # Shell chrome shares explorer.exe but must not count.
        self.assertFalse(is_app_window(
            "explorer.exe", "Progman", "Program Manager"))
        self.assertFalse(is_app_window("explorer.exe", "Shell_TrayWnd", ""))
        self.assertFalse(is_app_window(
            "explorer.exe", "DummyDWMListenerWindow", ""))

    def test_notepad_excludes_popup_hosts(self):
        self.assertTrue(is_app_window(
            "notepad.exe", "Notepad", "doc - Notepad"))
        self.assertFalse(is_app_window(
            "notepad.exe", "Microsoft.UI.Content.PopupWindowSiteBridge",
            "Pop-upHost"))

    def test_unknown_apps_need_title(self):
        self.assertTrue(is_app_window(
            "comet.exe", "Chrome_WidgetWin_1", "Some Page - Comet"))
        # Untitled menu popups must not count as browser windows.
        self.assertFalse(is_app_window(
            "comet.exe", "Chrome_WidgetWin_1", ""))
        self.assertTrue(is_app_window("notepad.exe", "Notepad", "doc"))


class TestAppIdentityMatches(unittest.TestCase):
    """Canonical app-identity check (Phase 8): normal processes match
    by executable basename; UWP-hosted windows match by title."""

    def test_normal_exe_match(self):
        self.assertTrue(app_identity_matches(
            "notepad", "notepad.exe", "doc - Notepad"))
        self.assertTrue(app_identity_matches(
            "windowsterminal", "WindowsTerminal.exe", "PowerShell"))
        self.assertTrue(app_identity_matches(
            "terminal", "windowsterminal.exe", "PowerShell"))

    def test_normal_exe_mismatch(self):
        self.assertFalse(app_identity_matches(
            "notepad", "explorer.exe", "Documents"))
        self.assertFalse(app_identity_matches(
            "comet", "notepad.exe", "doc - Notepad"))

    def test_uwp_host_matches_by_title(self):
        self.assertTrue(app_identity_matches(
            "calculator", "ApplicationFrameHost.exe", "Calculator"))
        self.assertTrue(app_identity_matches(
            "calc", "applicationframehost.exe", "Calculator"))

    def test_uwp_host_title_mismatch(self):
        self.assertFalse(app_identity_matches(
            "calculator", "ApplicationFrameHost.exe", "Settings"))
        self.assertFalse(app_identity_matches(
            "notepad", "ApplicationFrameHost.exe", "Calculator"))

    def test_empty_expectation_matches(self):
        self.assertTrue(app_identity_matches(
            "", "explorer.exe", "Documents"))


class TestBackgroundClassification(unittest.TestCase):
    def test_notepad_write_untested_not_assumed(self):
        res = classify_background("notepad.exe")
        self.assertEqual(res["verdict"], "WRITE_UNTESTED")
        self.assertTrue(res["read_without_focus"])

    def test_explorer_foreground_only(self):
        res = classify_background("explorer.exe")
        self.assertEqual(res["verdict"], "FOREGROUND_ONLY")

    def test_unknown_app_safe_default(self):
        res = classify_background("mystery-app")
        self.assertEqual(res["verdict"], "FOREGROUND_ONLY")


if __name__ == '__main__':
    unittest.main()
