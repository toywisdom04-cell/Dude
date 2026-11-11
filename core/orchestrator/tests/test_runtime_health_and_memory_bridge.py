import time
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.brain import Brain, Provider
from core.capability_bus import CapabilityBus
from core.orchestrator.perception_service import (
    ChangeClass,
    ObservationClass,
    PerceptionService,
    SemanticObservation,
)


class _Memory:
    def __init__(self):
        self.facts = []

    def remember_fact(self, fact, category="general"):
        self.facts.append((fact, category))
        return True


class _SemanticMemory(_Memory):
    def __init__(self):
        super().__init__()
        self.semantic = []

    def remember_semantic(self, fact, **kwargs):
        self.semantic.append((fact, kwargs))
        return True

    def recall_semantic(self, query="", limit=10):
        return [{"fact": fact} for fact, _ in self.semantic if query.lower() in fact.lower()]


class RuntimeHealthAndMemoryBridgeTest(unittest.TestCase):
    def test_provider_health_contains_no_secret_and_tracks_cooldown(self):
        provider = Provider.__new__(Provider)
        provider.name = "test"
        provider.model = "fast-model"
        provider.base_url = "https://example.invalid/v1"
        provider.health_state = "unknown"
        provider.last_error_kind = ""
        provider.last_error = ""
        provider.last_error_at = 0.0
        provider.last_success_at = 0.0
        provider.success_count = 0
        provider.failure_count = 0
        provider.cooldown_until = 0.0
        provider.key_available = True

        brain = Brain.__new__(Brain)
        brain.providers = [provider]
        brain._fail_until = {}
        brain._url_cooldown = {}
        brain._defs = {}
        brain._cooldown("test", 30, "rate_limited",
                        url=provider.base_url)
        brain.note_provider_failure(
            provider,
            "401 Bearer sk-secret-value",
            "auth",
        )
        row = brain.provider_health()["providers"][0]
        self.assertEqual(row["state"], "cooldown")
        self.assertEqual(row["last_error_kind"], "auth")
        self.assertNotIn("sk-secret-value", row["last_error"])
        self.assertGreater(row["cooldown_seconds"], 0)

        brain._clear_cooldown("test")
        row = brain.provider_health()["providers"][0]
        self.assertEqual(row["state"], "healthy")
        self.assertEqual(row["success_count"], 1)

    def test_provider_lanes_prefer_fast_and_deep_models_without_removing_fallbacks(self):
        fast = SimpleNamespace(name="fast", model="qwen3-flash",
                               lane="", config_order=0)
        deep = SimpleNamespace(name="deep", model="nemotron-ultra",
                               lane="", config_order=1)
        normal = SimpleNamespace(name="normal", model="general-model",
                                 lane="", config_order=2)
        self.assertEqual(
            [p.name for p in sorted((normal, deep, fast),
                                    key=lambda p: Brain._lane_rank(p, "fast"))],
            ["fast", "normal", "deep"],
        )
        self.assertEqual(
            [p.name for p in sorted((normal, fast, deep),
                                    key=lambda p: Brain._lane_rank(p, "deep"))],
            ["deep", "normal", "fast"],
        )

    def test_capability_bus_exposes_provider_status(self):
        brain = SimpleNamespace(provider_health=lambda: {
            "active_provider": "test",
            "providers": [{"name": "test", "state": "healthy"}],
            "checked_at": time.time(),
        })
        bus = CapabilityBus()
        bus.attach(brain=brain)
        result = bus.request("PROVIDER_STATUS")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["active_provider"], "test")

    def test_meaningful_perception_promotes_structured_fact_only(self):
        memory = _Memory()
        service = PerceptionService(SimpleNamespace(), memory=memory)
        meaningful = SemanticObservation(
            captured_at=time.time(),
            active_app="notepad.exe",
            window_title="Notes",
            change=ChangeClass.ACTION_RELEVANT.value,
            summary="A dialog appeared",
            dialog_kind="error",
        )
        transient = SemanticObservation(
            captured_at=time.time(),
            active_app="notepad.exe",
            window_title="Notes",
            change=ChangeClass.IRRELEVANT.value,
            summary="cursor repaint",
        )
        self.assertEqual(service.classify_observation(meaningful)[0],
                         ObservationClass.TASK_RELEVANT)
        with patch("core.associations.get_associations") as associations:
            self.assertTrue(service.promote(meaningful))
            associations.return_value.record_analytics.assert_called_once()
        self.assertFalse(service.promote(transient))
        self.assertEqual(len(memory.facts), 1)
        self.assertIn("dialog=error", memory.facts[0][0])
        self.assertEqual(memory.facts[0][1], "screen_state")

    def test_user_memory_tool_uses_semantic_provenance_when_available(self):
        from core.tools import recall_about_user, remember_about_user

        memory = _SemanticMemory()
        result = remember_about_user(memory, {
            "fact": "The content workflow uses ten-second video segments.",
            "category": "work",
            "subject": "content_workflow",
        })
        self.assertIn("Remembered", result)
        self.assertEqual(memory.semantic[0][1]["source"], "user_stated")
        self.assertEqual(memory.semantic[0][1]["subject"], "content_workflow")
        recalled = recall_about_user(memory, {"query": "ten-second"})
        self.assertIn("ten-second", recalled)

    def test_explicit_watch_mode_has_no_default_restart_cap(self):
        from core.experience import ExperienceLearner

        with tempfile.TemporaryDirectory() as data_dir:
            cfg = SimpleNamespace(
                data_dir=data_dir,
                get=lambda *args, **kwargs: kwargs.get("default"),
            )
            with patch("core.experience.get_config", return_value=cfg):
                learner = ExperienceLearner(_Memory(), data_dir)
            self.assertEqual(learner.max_watch_restarts, 0)


if __name__ == "__main__":
    unittest.main()
