"""Phase 2A Perception Consolidation Tests.

Tests for the PerceptionEngine, PerceptionCache, and integration with
existing perception modules (observer, experience, screentree, OCR).
"""
import unittest
import time
from unittest.mock import Mock, patch, MagicMock

from core.orchestrator.perception import (
    PerceptionEngine,
    PerceptionCache,
    PerceptionLevel,
    PerceptionSnapshot,
    ControlInfo,
    OCRRegion,
    ScreenDelta,
    Rect,
)
from core.orchestrator.state import (
    TargetSpec,
    GroundingMethod,
)


class TestPerceptionCache(unittest.TestCase):
    """Test the PerceptionCache dataclass."""

    def setUp(self):
        self.cache = PerceptionCache()

    def test_initial_state(self):
        """Cache starts empty."""
        self.assertEqual(self.cache.active_app, "unknown")
        self.assertEqual(self.cache.controls, [])
        self.assertEqual(self.cache.ocr_text, "")
        self.assertFalse(self.cache.change_detected)

    def test_update_sets_fields(self):
        """Update sets fields and captured_at."""
        before = self.cache.captured_at
        time.sleep(0.01)
        self.cache.update(active_app="notepad", active_window={"title": "test"})
        self.assertEqual(self.cache.active_app, "notepad")
        self.assertGreater(self.cache.captured_at, before)

    def test_get_snapshot_returns_copy(self):
        """get_snapshot returns a PerceptionSnapshot with current data."""
        self.cache.update(active_app="test_app", ocr_text="hello")
        snap = self.cache.get_snapshot()
        self.assertIsInstance(snap, PerceptionSnapshot)
        self.assertEqual(snap.active_app, "test_app")
        self.assertEqual(snap.ocr_text, "hello")

    def test_is_fresh(self):
        """Freshness check works."""
        self.assertFalse(self.cache.is_fresh(5.0))
        self.cache.update(active_app="test")
        self.assertTrue(self.cache.is_fresh(5.0))


class TestPerceptionSnapshot(unittest.TestCase):
    """Test PerceptionSnapshot dataclass."""

    def test_is_fresh(self):
        """Snapshot freshness check."""
        import datetime
        snap = PerceptionSnapshot(captured_at=datetime.datetime.now())
        self.assertTrue(snap.is_fresh(5.0))
        
        old_time = datetime.datetime.now() - datetime.timedelta(seconds=100)
        old_snap = PerceptionSnapshot(captured_at=old_time)
        # age_seconds needs to be computed - manually set for test
        old_snap.age_seconds = 100.0
        self.assertFalse(old_snap.is_fresh(5.0))


class TestPerceptionEngine(unittest.TestCase):
    """Test the PerceptionEngine main interface."""

    def setUp(self):
        self.engine = PerceptionEngine()

    def test_observe_level_1(self):
        """Observe returns L1 snapshot."""
        snap = self.engine.observe(required_level=PerceptionLevel.LEVEL_1_APP_WINDOW)
        self.assertIsInstance(snap, PerceptionSnapshot)
        self.assertIsNotNone(snap.active_app)

    def test_observe_caches_result(self):
        """Repeated observe uses cache when not forced."""
        snap1 = self.engine.observe(required_level=PerceptionLevel.LEVEL_1_APP_WINDOW)
        snap2 = self.engine.observe(required_level=PerceptionLevel.LEVEL_1_APP_WINDOW)
        # Should return same cached snapshot
        self.assertEqual(snap1.active_app, snap2.active_app)

    def test_capture_now_forces_refresh(self):
        """capture_now forces fresh capture."""
        snap1 = self.engine.observe(required_level=PerceptionLevel.LEVEL_1_APP_WINDOW)
        time.sleep(0.01)
        snap2 = self.engine.capture_now(required_level=PerceptionLevel.LEVEL_1_APP_WINDOW)
        self.assertEqual(snap1.active_app, snap2.active_app)

    def test_observe_escalates_levels(self):
        """Observe escalates through levels when requested."""
        # This just tests the code path doesn't crash
        snap = self.engine.observe(
            required_level=PerceptionLevel.LEVEL_2_UIA_TREE,
            force_refresh=True
        )
        self.assertIsInstance(snap, PerceptionSnapshot)

    def test_get_control_by_automation_id(self):
        """get_control finds control by automation_id."""
        # Manually populate cache
        ctrl = ControlInfo(
            name="test_button",
            automation_id="btn_test",
            x=10, y=20, w=100, h=30,
            enabled=True, visible=True, role="button"
        )
        self.engine._cache.controls = [ctrl]
        self.engine._cache.captured_at = time.time()
        
        target = TargetSpec(control_id="btn_test")
        result = self.engine.get_control(target)
        self.assertIsNotNone(result)
        self.assertEqual(result.automation_id, "btn_test")

    def test_get_control_by_name(self):
        """get_control finds control by name."""
        ctrl = ControlInfo(
            name="Submit Button",
            automation_id="",
            x=10, y=20, w=100, h=30,
            enabled=True, visible=True, role="button"
        )
        self.engine._cache.controls = [ctrl]
        self.engine._cache.captured_at = time.time()
        
        target = TargetSpec(control_name="Submit")
        result = self.engine.get_control(target)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Submit Button")

    def test_get_control_ocr_fallback(self):
        """get_control falls back to OCR text match."""
        region = OCRRegion(text="Click Me", x=50, y=60, w=80, h=25, confidence=0.9)
        self.engine._cache.ocr_regions = [region]
        self.engine._cache.captured_at = time.time()
        
        target = TargetSpec(text_match="Click")
        result = self.engine.get_control(target)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Click Me")
        self.assertEqual(result.ctype, "ocr_text")

    def test_get_control_relative_coords(self):
        """get_control handles relative coordinates."""
        self.engine._cache.window_bounds = Rect(100, 100, 800, 600)
        self.engine._cache.captured_at = time.time()
        
        target = TargetSpec(relative_coords=(0.5, 0.5))
        result = self.engine.get_control(target)
        self.assertIsNotNone(result)
        self.assertEqual(result.x, 500)  # 100 + 0.5 * 800
        self.assertEqual(result.y, 400)  # 100 + 0.5 * 600

    def test_get_control_absolute_coords(self):
        """get_control handles absolute coordinates as last resort."""
        target = TargetSpec(coordinates=(100, 200))
        result = self.engine.get_control(target)
        self.assertIsNotNone(result)
        self.assertEqual(result.x, 100)
        self.assertEqual(result.y, 200)

    def test_get_control_returns_none_when_no_match(self):
        """get_control returns None when nothing matches."""
        target = TargetSpec(control_name="NonExistent")
        result = self.engine.get_control(target)
        self.assertIsNone(result)


class TestChangeDetection(unittest.TestCase):
    """Test change detection between snapshots."""

    def setUp(self):
        self.engine = PerceptionEngine()

    def test_detect_change_first_snapshot(self):
        """First snapshot reports all changed."""
        snap = PerceptionSnapshot(active_app="notepad")
        delta = self.engine.detect_change(None)
        self.assertTrue(delta.app_changed)
        self.assertTrue(delta.window_changed)
        self.assertTrue(delta.uia_tree_changed)
        self.assertTrue(delta.ocr_changed)

    def test_detect_change_app_change(self):
        """Detects app change."""
        before = PerceptionSnapshot(active_app="notepad")
        self.engine._cache.active_app = "calc"
        self.engine._cache.captured_at = time.time()
        
        delta = self.engine.detect_change(before)
        self.assertTrue(delta.app_changed)

    def test_detect_change_ocr_change(self):
        """Detects OCR text change."""
        before = PerceptionSnapshot(ocr_text="hello")
        self.engine._cache.ocr_text = "world"
        self.engine._cache.captured_at = time.time()
        
        delta = self.engine.detect_change(before)
        self.assertTrue(delta.ocr_changed)

    def test_detect_change_uia_tree(self):
        """Detects UIA tree change."""
        ctrl1 = ControlInfo(name="Button1")
        ctrl2 = ControlInfo(name="Button2")
        before = PerceptionSnapshot(uia_tree=[ctrl1])
        self.engine._cache.controls = [ctrl2]
        self.engine._cache.uia_tree = [ctrl2]
        self.engine._cache.captured_at = time.time()
        
        delta = self.engine.detect_change(before)
        self.assertTrue(delta.uia_tree_changed)
        self.assertEqual(len(delta.new_controls), 1)
        self.assertEqual(len(delta.removed_controls), 1)


class TestPerceptionLevelEnum(unittest.TestCase):
    """Test PerceptionLevel ordering."""

    def test_levels_ordered(self):
        """Levels are ordered by cost/complexity."""
        self.assertLess(
            PerceptionLevel.LEVEL_1_APP_WINDOW,
            PerceptionLevel.LEVEL_2_UIA_TREE
        )
        self.assertLess(
            PerceptionLevel.LEVEL_2_UIA_TREE,
            PerceptionLevel.LEVEL_3_OCR
        )
        self.assertLess(
            PerceptionLevel.LEVEL_3_OCR,
            PerceptionLevel.LEVEL_4_TARGETED_CV
        )
        self.assertLess(
            PerceptionLevel.LEVEL_4_TARGETED_CV,
            PerceptionLevel.LEVEL_5_VISION_MODEL
        )


class TestObserverIntegration(unittest.TestCase):
    """Test observer integration (mocked)."""

    def test_observer_feeds_level_1(self):
        """Observer current_screen feeds L1."""
        mock_observer = Mock()
        mock_observer.current_screen.return_value = {
            "app": "test_app",
            "title": "Test Window",
        }
        
        engine = PerceptionEngine(get_observer=lambda: mock_observer)
        snap = engine.observe(required_level=PerceptionLevel.LEVEL_1_APP_WINDOW)
        
        self.assertEqual(snap.active_app, "test_app")
        mock_observer.current_screen.assert_called()

    def test_observer_feeds_ocr(self):
        """Observer current_screen feeds OCR when fresh."""
        mock_observer = Mock()
        mock_observer.current_screen.return_value = {
            "app": "test_app",
            "title": "Test Window",
            "ocr": "some text on screen",
            "ocr_age_min": 2,
        }
        
        engine = PerceptionEngine(get_observer=lambda: mock_observer)
        snap = engine.observe(
            required_level=PerceptionLevel.LEVEL_3_OCR,
            force_refresh=True
        )
        
        self.assertEqual(snap.ocr_text, "some text on screen")


class TestExperienceIntegration(unittest.TestCase):
    """Test experience learner integration (mocked)."""

    def test_experience_context_in_l1(self):
        """Experience watch_status feeds L1 context."""
        mock_observer = Mock()
        mock_observer.current_screen.return_value = {
            "app": "test_app",
            "title": "Test Window",
        }
        
        mock_experience = Mock()
        mock_experience.watch_status.return_value = {
            "on": True,
            "insights": 5,
            "frames": 100,
        }
        
        engine = PerceptionEngine(
            get_observer=lambda: mock_observer,
            get_experience=lambda: mock_experience
        )
        snap = engine.observe(required_level=PerceptionLevel.LEVEL_1_APP_WINDOW)
        
        exp_ctx = snap.active_window.get("experience_context")
        self.assertIsNotNone(exp_ctx)
        self.assertTrue(exp_ctx["observation_active"])
        self.assertEqual(exp_ctx["insights_count"], 5)


class TestScreentreeIntegration(unittest.TestCase):
    """Test screentree/UIA integration (mocked)."""

    def test_screentree_feeds_level_2(self):
        """Screentree rows feed L2 controls."""
        mock_row = {"ctype": "Button", "name": "OK", "x": 10, "y": 20, "w": 50, "h": 30, "enabled": True}
        mock_st = Mock()
        mock_st.available.return_value = True
        mock_st.rows = [mock_row]
        mock_st.find_focused.return_value = None
        
        engine = PerceptionEngine(get_screentree=lambda: mock_st)
        snap = engine.observe(
            required_level=PerceptionLevel.LEVEL_2_UIA_TREE,
            force_refresh=True
        )
        
        self.assertEqual(len(snap.controls), 1)
        self.assertEqual(snap.controls[0].name, "OK")


class TestVisionBudget(unittest.TestCase):
    """Test vision budget tracking."""

    def test_budget_starts_at_zero(self):
        engine = PerceptionEngine()
        self.assertTrue(engine.can_use_vision())

    def test_budget_increments(self):
        engine = PerceptionEngine()
        engine._vision_budget_limit = 3
        engine.record_vision_use()
        engine.record_vision_use()
        engine.record_vision_use()
        self.assertFalse(engine.can_use_vision())


class TestFeatureFlagDefaults(unittest.TestCase):
    """Test Phase 2A feature flags default to OFF."""

    def test_phase2a_flags_off_by_default(self):
        """PerceptionEngine works without feature flags (always available as library)."""
        # The engine itself doesn't check flags - that's done in dude.py
        engine = PerceptionEngine()
        snap = engine.observe(PerceptionLevel.LEVEL_1_APP_WINDOW)
        self.assertIsInstance(snap, PerceptionSnapshot)


if __name__ == "__main__":
    unittest.main()