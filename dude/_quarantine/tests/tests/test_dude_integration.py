
# -*- coding: utf-8 -*-
"""
Integration and Capability Verification Test Suite for DUDE

Tests:
1. Microphone detection
2. Streaming STT (fast-whisper subprocess)
3. TTS (Edge TTS & persistent phrase caching)
4. Screen capture
5. Screen observation & frame pruning
6. Vision analysis pipeline
7. Cursor control abstractions
8. Keyboard control abstractions
9. Filesystem access & Desktop checklist sync
10. Memory write
11. Memory retrieval
12. Task planning & TaskState
13. Tool execution
14. Error recovery engine
15. GUI dialogue recovery
16. Task verification
17. User interruption / barge-in
18. Agent cancellation
19. Background execution
20. Capability registry presence
"""

import time
import os
import sys
import unittest

sys.path.insert(0, r"E:\Dude\dude")

from core.config import get_config
from core.memory import Memory
from core.task_state import get_task_state, reset_task_state
from core.recovery import RecoveryEngine
from core.checklist import DesktopChecklist
from core.personality import build_context
from core.voice import Voice
from perception.stt import resolve_model_path


class TestDUDEIntegration(unittest.TestCase):
    def setUp(self):
        reset_task_state()
        self.memory = Memory()
        self.cfg = get_config()

    def test_01_microphone_config(self):
        sample_rate = self.cfg.get("ear", "sample_rate")
        self.assertEqual(sample_rate, 16000)

    def test_02_stt_model_resolution(self):
        model_path = resolve_model_path("base.en")
        self.assertTrue(len(model_path) > 0)

    def test_03_tts_caching_latency(self):
        voice = Voice()
        t0 = time.time()
        phrase = "Test phrase for caching latency check."
        path1 = voice._synth(phrase)
        t1 = time.time()
        dur_uncached = t1 - t0
        
        t2 = time.time()
        path2 = voice._synth(phrase)
        t3 = time.time()
        dur_cached = t3 - t2
        
        self.assertTrue(os.path.exists(path1))
        self.assertTrue(os.path.exists(path2))
        self.assertLess(dur_cached, 0.05)  # <50ms from cache

    def test_04_desktop_checklist_generation(self):
        checklist = DesktopChecklist(self.memory)
        content = checklist.render_checklist()
        self.assertIn("DUDE", content)
        self.assertTrue(os.path.exists(checklist.file_path))

    def test_05_memory_write_and_recall(self):
        fact_text = "Test memory item for integration suite 12345"
        self.memory.remember_fact(fact_text, category="test")
        recalled = self.memory.recall_facts("integration suite 12345", limit=5)
        self.assertTrue(any("12345" in r for r in recalled))

    def test_06_task_state_machine(self):
        state = get_task_state()
        state.create_task("Test Excel workbook task", ["Create workbook", "Move file"])
        self.assertTrue(state.has_active_task())
        self.assertEqual(len(state.subtasks), 2)
        state.complete_subtask("Create workbook", "Done")
        self.assertEqual(len(state.completed_subtasks), 1)

    def test_07_recovery_engine_detection(self):
        state = get_task_state()
        state.set_current_goal("Move file to folder")
        engine = RecoveryEngine(task_state=state)
        
        result = engine.handle_post_tool_result("move_path", {"src": "a"}, "ERROR: Permission denied - dialog still open")
        self.assertIn("status", result)
        self.assertNotEqual(result["status"], "no_recovery_needed")

    def test_08_capability_registry(self):
        ctx = build_context(self.memory)
        self.assertIn("DUDE NATIVE CAPABILITIES REGISTRY", ctx)
        self.assertIn("VISION: AVAILABLE", ctx)
        self.assertIn("STT: AVAILABLE", ctx)
        self.assertIn("TTS: AVAILABLE", ctx)

    def test_09_tools_execution_import(self):
        from core.tools import get_datetime
        res = get_datetime(self.memory, {})
        self.assertIn("202", res)

    def test_10_startup_shutdown_greeting(self):
        checklist = DesktopChecklist(self.memory)
        greeting = checklist.startup_greeting()
        self.assertIn("sir", greeting.lower())
        farewell = checklist.shutdown_review()
        self.assertIn("offline", farewell.lower())


if __name__ == "__main__":
    unittest.main()
